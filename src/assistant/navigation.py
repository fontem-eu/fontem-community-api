"""Letting the assistant move the user around the site.

Two halves. The model needs to know *where the user is* and *what pages
exist*; and when it decides to move them, that has to happen in the
browser, which is the one place this process cannot reach.

The site map is not stored here. The frontend generates it from its own
router (``src/generated/route-manifest.json``, kept honest by a drift
test) and sends it with each turn. Keeping a second copy server-side would
mean two sources of truth for a thing whose entire value is being accurate
about what the app serves — and the copies would diverge the first time
someone added a route.

Navigation is modelled as a side effect that reports success immediately
rather than a tool that waits for the browser. The transport is one-way
SSE; inventing a back-channel so the model could hear "yes, I arrived"
would buy nothing, because there is nothing useful for it to do with the
answer that it cannot do by assuming the push succeeded. If the path is
invalid we know that here, synchronously, and say so.
"""
from __future__ import annotations

import json
import re

NAVIGATE_TOOL_NAME = "navigate"

#: Cap on how many routes we describe to the model. The manifest is ~60
#: navigable entries today; this exists so a future explosion of routes
#: degrades the prompt gracefully instead of silently blowing the context.
MAX_ROUTES_IN_PROMPT = 200


def navigate_tool_schema() -> dict:
    """OpenAI-format function declaration for the navigate tool."""
    return {
        "type": "function",
        "function": {
            "name": NAVIGATE_TOOL_NAME,
            "description": (
                "Take the user to a page in the app. Use this when the answer "
                "is somewhere on the site rather than something you should "
                "retype — e.g. they ask to see the Atlas, a company page, or "
                "their own stories. Only use paths from the site map in your "
                "system context. Fill in any :params with real values. Say "
                "what you are doing and why in your reply; do not navigate "
                "silently."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Concrete path to navigate to, params substituted, "
                            "e.g. '/c/AAPL/summary' — not '/c/:ticker/:view'."
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": "One short phrase shown to the user, e.g. 'Opening the Atlas'.",
                    },
                },
                "required": ["path"],
            },
        },
    }


def _pattern_to_regex(pattern: str) -> str:
    """`/c/:ticker/:view` -> a regex matching `/c/AAPL/summary`."""
    out = []
    for part in pattern.split("/"):
        if not part:
            continue
        out.append(r"[^/]+" if part.startswith(":") else re.escape(part))
    return "^/" + "/".join(out) + "/?$" if out else "^/$"


def validate_path(path: str, routes: list[dict]) -> tuple[bool, str]:
    """Is `path` somewhere the client said it can actually go?

    Returns (ok, reason). Validating against the routes the *client* sent
    means we can never authorise a path this build of the frontend cannot
    serve, which a server-side copy of the manifest could.
    """
    if not path or not path.startswith("/"):
        return False, "path must start with '/'"
    if "://" in path or path.startswith("//"):
        # An off-site "navigation" is an open redirect wearing a hat.
        return False, "only in-app paths are allowed"
    clean = path.split("?")[0].split("#")[0]
    for r in routes:
        if re.match(_pattern_to_regex(r.get("path", "")), clean):
            return True, r.get("path", "")
    return False, "no such page in the site map"


def system_context(nav: dict | None) -> str:
    """The 'where you are / what exists' block appended to the prompt.

    Empty string when the client sent nothing, so an older frontend simply
    gets an assistant that does not navigate rather than a broken one.
    """
    if not nav:
        return ""
    routes = (nav.get("routes") or [])[:MAX_ROUTES_IN_PROMPT]
    if not routes:
        return ""

    lines = ["", "## Where the user is", ""]
    current = nav.get("current")
    lines.append(f"Current page: {current}" if current else "Current page: unknown")
    title = nav.get("title")
    if title:
        lines.append(f"Page title: {title}")

    lines += ["", "## Site map", "",
              "Pages you can send the user to with the `navigate` tool.",
              "`:param` segments must be filled in with real values.",
              "Routes marked (auth) need the user to be signed in.", ""]
    for r in routes:
        bits = [f"- `{r.get('path')}`"]
        if r.get("requires_auth"):
            bits.append("(auth)")
        desc = r.get("description")
        if desc:
            bits.append(f"— {desc}")
        lines.append(" ".join(bits))
    return "\n".join(lines)


def navigate_result(path: str, routes: list[dict]) -> tuple[str, dict | None]:
    """Handle a navigate call.

    Returns the tool result the model sees, and the payload to emit to the
    browser (or None when the path was rejected, so a bad path moves
    nobody's screen).
    """
    ok, why = validate_path(path, routes)
    if not ok:
        return json.dumps({"ok": False, "error": why, "path": path}), None
    return json.dumps({"ok": True, "navigated_to": path}), {"path": path}
