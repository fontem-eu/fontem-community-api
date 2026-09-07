#!/usr/bin/env python3
"""Drive the assistant through writing one data story, and record everything.

NOT a gate. Nothing in CI runs this and nothing should: it talks to a live
environment, spends model tokens, and its output is a thing to READ, not a
pass/fail. `evals/` is outside pytest's testpaths for exactly that reason.

The problem it exists for: iterating on the assistant meant deploying and
then driving it by hand through the UI, which is slow enough that you stop
doing it. This does one full pass headlessly against **testing** — which
reads the same fontem-shared graph production does — and writes a report
with the two things worth looking at:

  * the REASONING: every tool call in order, its arguments, and what came
    back. Where a turn goes wrong is almost always visible here and almost
    never visible in the finished prose.
  * the ARTIFACTS: the article body as it ended up, the Studio projects and
    plots that were created, and which proposals were applied or refused.

Proposals are applied here rather than in a browser. The frontend's applier
is unit-tested; what this is for is the server half and the model's
behaviour, and driving Chromium to click Apply would make the loop slow
enough to stop using again.

    python evals/story_loop.py --model minimax-m3
    python evals/story_loop.py --model qwen3-8b --prompt-file my_prompt.txt

Credentials come from the environment or, failing that, the smoke secret in
the target namespace via kubectl -- the same account the e2e suite uses.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from html.parser import HTMLParser

#: The default brief. Deliberately the user's own wording: the point is to
#: watch the assistant handle a real request, not a request engineered to
#: be easy for it.
DEFAULT_PROMPT = (
    "Hi! I want to write a data story analyzing the EU's public expenses in "
    "Russian companies before and after the start of the invasion of "
    "Ukraine. To that end, I want you to create plots in data studio of the "
    "public expenses across EU countries and industry sectors, before and "
    "after the war. Then add these plots to the current data story and add "
    "some text explaining the plots and the data."
)

RESULTS = Path(__file__).parent / "results"


def _kubectl_secret(ns: str, name: str, key: str) -> str:
    try:
        raw = subprocess.run(
            ["kubectl", "-n", ns, "get", "secret", name,
             "-o", f"jsonpath={{.data.{key}}}"],
            capture_output=True, text=True, check=True, timeout=30).stdout
        import base64
        return base64.b64decode(raw).decode().strip()
    except Exception:                       # pylint: disable=broad-except
        return ""


def credentials(namespace: str) -> tuple[str, str]:
    email = os.environ.get("TEST_EMAIL") or _kubectl_secret(
        namespace, "gmr-smoke-creds", "email")
    password = os.environ.get("TEST_PASSWORD") or _kubectl_secret(
        namespace, "gmr-smoke-creds", "password")
    return email, password


class _HtmlToTipTap(HTMLParser):
    """Just enough HTML to apply what a first draft proposes.

    The browser does this properly, via TipTap's own schema. Here it only
    has to be faithful enough that the NEXT turn reads back something like
    what the model wrote — otherwise the follow-up faces a blank article
    and focused editing cannot even be attempted, which is exactly what
    made the first multi-turn run useless.

    Models also wrap drafts in a whole <!DOCTYPE html> document; the
    structural tags are simply ignored rather than becoming headings.
    """

    _BLOCKS = {"p": "paragraph", "h1": "heading", "h2": "heading",
               "h3": "heading", "li": "paragraph"}
    _SKIP = {"html", "head", "body", "meta", "title", "style", "script",
             "table", "thead", "tbody", "tr", "td", "th"}

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[dict] = []
        self._tag: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        del attrs
        if tag in self._BLOCKS:
            self._flush()
            self._tag = tag

    def handle_endtag(self, tag):
        if tag in self._BLOCKS:
            self._flush()

    def handle_data(self, data):
        if self._tag and data.strip():
            self._buf.append(data)

    def _flush(self) -> None:
        text = " ".join(" ".join(self._buf).split())
        if text and self._tag:
            block = {"type": self._BLOCKS[self._tag],
                     "content": [{"type": "text", "text": text}]}
            if self._tag.startswith("h"):
                block["attrs"] = {"level": int(self._tag[1])}
            self.blocks.append(block)
        self._tag, self._buf = None, []

    def close(self):
        super().close()
        self._flush()


def html_to_tiptap(html: str) -> dict:
    parser = _HtmlToTipTap()
    parser.feed(html)
    parser.close()
    return {"type": "doc", "content": parser.blocks}


class Loop:
    """One pass: create an article, ask for the story, record what happened."""

    def __init__(self, http: httpx.AsyncClient, base: str, token: str) -> None:
        self._http = http
        self._base = base.rstrip("/")
        self._auth = {"Authorization": f"Bearer {token}"}
        self.calls: list[dict] = []
        self.proposals: list[dict] = []
        self.reply: list[str] = []
        self.errors: list[str] = []
        self.usage: dict = {}
        #: Index into `calls` where each follow-up turn began,
        #: so the report can show which turn did what.
        self.turn_boundaries: list[int] = []
        #: How far into `calls` the applier has already gone.
        self.applied_through: int = 0

    async def _api(self, method: str, path: str, **kw):
        return await self._http.request(
            method, f"{self._base}/capi{path}", headers=self._auth, **kw)

    async def create_article(self, title: str) -> str:
        r = await self._api("POST", "/data-stories",
                            json={"title": title, "abstract": "", "tags": []})
        r.raise_for_status()
        return r.json()["id"]

    async def read_document(self, report_id: str):
        r = await self._api("GET", f"/data-stories/{report_id}")
        r.raise_for_status()
        return r.json()

    async def save_document(self, report_id: str, tiptap, base_revision=None):
        """Apply an edit the way the browser would, minus the browser."""
        body = {"tiptap": tiptap, "version": 2, "author_kind": "assistant"}
        if base_revision:
            body["base_revision"] = base_revision
        r = await self._api("PUT", f"/data-stories/{report_id}/content",
                            json=body)
        if r.status_code >= 400:
            self.errors.append(f"save_document {r.status_code}: {r.text[:200]}")
            return None
        return r.json().get("revision")

    async def set_title(self, report_id: str, title: str):
        r = await self._api("PUT", f"/data-stories/{report_id}",
                            json={"title": title})
        if r.status_code >= 400:
            self.errors.append(f"set_title {r.status_code}: {r.text[:160]}")

    async def studio_projects(self):
        r = await self._api("GET", "/studio/projects")
        return r.json() if r.status_code < 400 else []

    async def turn(self, report_id: str, message: str, conversation_key: str):
        """One assistant turn, streamed, with every event recorded."""
        payload = {
            "message": message,
            "conversation_key": conversation_key,
            "report_id": report_id,
            # The article is open in the editor. Without this the document
            # verbs are not even offered -- see EDITOR_ONLY_TOOLS.
            "has_editor": True,
            "nav": {"path": f"/stories/{report_id}/edit", "routes": []},
        }
        async with self._http.stream(
            "POST", f"{self._base}/capi/assist/chat/stream",
            headers=self._auth, json=payload, timeout=900.0,
        ) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode()[:400]
                self.errors.append(f"turn {resp.status_code}: {body}")
                return
            event = None
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    self._event(event, line.split(":", 1)[1].strip())

    def _event(self, event: str | None, raw: str) -> None:
        """One SSE event.

        The vocabulary is not what it looks like from the outside. There is
        no `tool_use` EVENT: a call announces itself as `status` with
        `phase: "tool_use"`, and the arguments arrive again on the
        `tool_result` event (see assistant/tool_trace.trace). Reading them
        off the result is what makes the trace complete -- the first pass
        of this harness keyed on an event name that never fires and
        recorded every call with empty arguments, which hid half of what
        the model actually did.
        """
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return
        if event == "chunk":
            self.reply.append(data.get("text") or "")
        elif event == "tool_result":
            self.calls.append({
                "tool": data.get("tool", "?"),
                "args": data.get("args") or {},
                "result": data.get("result", ""),
                "bytes": data.get("bytes"),
                "truncated": bool(data.get("truncated")),
                "elapsed": data.get("elapsed"),
            })
        elif event == "status":
            if data.get("phase") == "tool_use" and data.get("proposal"):
                self.proposals.append(data["proposal"])
            elif data.get("phase") == "truncated":
                self.errors.append(f"truncated: {json.dumps(data)[:200]}")
        elif event == "error":
            self.errors.append(json.dumps(data)[:300])
        elif event == "usage":
            self.usage = data


def article_doc(article: dict) -> dict:
    """The TipTap document out of a GET /data-stories/{id} response.

    The field is `content_doc`, and it holds the STORED shape --
    {"tiptap": doc, "version": n} -- not the document. Reading
    `content_json` (which does not exist on this response) returned None,
    so the harness started every apply from an empty document and reported
    every finished article as "0 blocks" while 15 blocks sat in the
    database. A report that under-counts the artifacts is worse than one
    that fails: it reads as the model having done nothing.
    """
    doc = article.get("content_doc") or {}
    if isinstance(doc, dict) and isinstance(doc.get("tiptap"), dict):
        return doc["tiptap"]
    return doc if isinstance(doc, dict) else {}


async def apply_proposals(loop: Loop, report_id: str) -> list[str]:
    """Apply what the model proposed, the way the editor would.

    Only the two verbs this brief produces are handled. Anything else is
    reported rather than silently skipped -- an unapplied proposal that
    nobody notices is how a run looks better than it was.
    """
    applied = []
    # Only calls made SINCE the last apply. This walked the whole list
    # every time, so turn 1's proposals were re-applied after turn 2 --
    # the article got its charts inserted twice, the counts came out
    # "proposed: 10, applied: 11", and the second save collided with the
    # first on the revision check.
    new_calls = loop.calls[loop.applied_through:]
    loop.applied_through = len(loop.calls)
    article = await loop.read_document(report_id)
    tiptap = article_doc(article) or {"type": "doc", "content": []}
    # The save is concurrency-checked: "a save that does not name its
    # baseline is refused rather than guessed at". Applying between turns
    # without naming one earned a 409 and lost the turn's edits.
    base = article.get("head_revision")
    for call in new_calls:
        try:
            result = json.loads(call.get("result") or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(result, dict) or result.get("error"):
            continue
        tool = call["tool"]
        if tool in ("mcp__gmr__replace_part", "mcp__gmr__replace_body"):
            if result.get("content_json"):
                # replace_part: the server already spliced it.
                tiptap = result["content_json"]
                applied.append(tool)
            elif (call.get("args") or {}).get("content"):
                # replace_body: the model sent HTML, which the browser
                # would hand to TipTap. Converted here so the follow-up
                # turn reads back a real article instead of a blank one.
                tiptap = html_to_tiptap(call["args"]["content"])
                applied.append(f"{tool} (html)")
        elif tool == "mcp__gmr__set_title":
            title = (call.get("args") or {}).get("title")
            if title:
                await loop.set_title(report_id, title)
                applied.append(tool)
        elif tool == "mcp__gmr__insert_studio_plot":
            node = {"type": "widget", "attrs": {
                "widget_type": "pipeline", "schema_version": 1,
                "data_params": result.get("data_params"),
                "ui_params": result.get("ui_params")}}
            blocks = list(tiptap.get("content") or [])
            at = result.get("at_block")
            at = len(blocks) if at is None else max(0, min(int(at), len(blocks)))
            blocks.insert(at, node)
            tiptap = {**tiptap, "content": blocks}
            applied.append(f"{tool}@{at}")
    if applied:
        await loop.save_document(report_id, tiptap, base_revision=base)
    return applied


def report(loop: Loop, meta: dict, applied: list[str], article, projects,
           pre_existing: set | None = None) -> str:
    out = [f"# Assistant story loop — {meta['started']}", ""]
    # First thing in the report, because a turn cut short upstream looks
    # exactly like a model that chose to stop -- and reading it as the
    # latter is how you "discover" behaviour that never happened. One run
    # ended after three calls on a Connection error and its trace was
    # briefly mistaken for the model declining to write anything.
    if loop.errors:
        out.append("> **THIS RUN IS NOT CLEAN — do not read behaviour from it.**")
        for e in loop.errors:
            out.append(f"> {e}")
        out.append("")
    out.append(f"model: **{meta['model']}**   base: {meta['base_url']}")
    out.append(f"article: `{meta['report_id']}`   turns: {meta['turns']}")
    out.append("")
    out.append("## Reasoning — every tool call, in order")
    out.append("")
    if not loop.calls:
        out.append("_No tool calls. The model answered from memory._")
    for i, c in enumerate(loop.calls, 1):
        if (i - 1) in loop.turn_boundaries:
            out.append("")
            out.append(f"--- turn {loop.turn_boundaries.index(i - 1) + 2} ---")
            out.append("")
        args = json.dumps(c.get("args") or {})[:400]
        res = (c.get("result") or "")[:400].replace("\n", " ")
        out.append(f"{i:>3}. `{c['tool']}`  {args}")
        out.append(f"     -> {res}")
    out.append("")
    out.append("## Proposals")
    out.append("")
    out.append(f"proposed: {len(loop.proposals)}   applied: {len(applied)}")
    for a in applied:
        out.append(f"  - applied {a}")
    unapplied = len(loop.proposals) - len(applied)
    if unapplied > 0:
        out.append(f"  - **{unapplied} proposed but not applied** "
                   "(refused, or a verb this harness does not apply)")
    out.append("")
    out.append("## Artifacts")
    out.append("")
    seen_before = pre_existing or set()
    fresh = [p for p in projects if p.get("id") not in seen_before]
    out.append(f"Studio projects now: {len(projects)}  "
               f"(**{len(fresh)} created by this run**)")
    # New first: what this run built is the thing being evaluated, and it
    # was previously indistinguishable from what the last run left behind.
    ordered = fresh + [p for p in projects if p.get("id") in seen_before]
    for p in ordered[:10]:
        plots = p.get("plots") or []
        mark = "NEW " if p.get("id") not in seen_before else "    "
        out.append(f"  - {mark}{p.get('name')!r}  plots={len(plots)} "
                   f"queries={len(p.get('queries') or [])}")
    out.append("")
    blocks = article_doc(article).get("content") or []
    widgets = [b for b in blocks if b.get("type") == "widget"]
    out.append(f"Article blocks: {len(blocks)}  (widgets embedded: {len(widgets)})")
    out.append("")
    out.append("### The article as it ended up")
    out.append("")
    out.append("```")
    for b in blocks:
        if b.get("type") == "widget":
            attrs = b.get("attrs") or {}
            out.append(f"[widget: {attrs.get('widget_type')}]")
        else:
            text = "".join(
                t.get("text", "") for t in (b.get("content") or [])
                if isinstance(t, dict))
            out.append(text or f"[{b.get('type')}]")
    out.append("```")
    out.append("")
    if loop.errors:
        out.append("## Errors")
        out.append("")
        for e in loop.errors:
            out.append(f"  - {e}")
    out.append("")
    out.append("## Assistant's reply")
    out.append("")
    out.append("".join(loop.reply).strip()[:4000] or "_(empty)_")
    return "\n".join(out)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="https://fontem.testing.void42.internal")
    ap.add_argument("--namespace", default="fontem-testing")
    ap.add_argument("--model", default="minimax-m3")
    ap.add_argument("--prompt-file", help="override the default brief")
    ap.add_argument("--turns", type=int, default=1,
                    help="follow-up turns saying 'continue', for a model "
                         "that stops mid-plan")
    ap.add_argument("--then", action="append", default=[], metavar="MESSAGE",
                    help="a scripted follow-up turn, repeatable. A real "
                         "iteration is a conversation, and the focused-edit "
                         "verbs only come up on a SECOND pass -- the first "
                         "is a blank article, where a whole-body draft is "
                         "the right call.")
    args = ap.parse_args()

    email, password = credentials(args.namespace)
    if not email or not password:
        print("no credentials: set TEST_EMAIL/TEST_PASSWORD or grant "
              "kubectl access to gmr-smoke-creds", file=sys.stderr)
        return 2
    prompt = (Path(args.prompt_file).read_text(encoding="utf-8")
              if args.prompt_file else DEFAULT_PROMPT)

    async with httpx.AsyncClient(verify=False, timeout=120.0) as http:
        login = await http.post(f"{args.base_url}/capi/auth/login",
                                json={"email": email, "password": password})
        login.raise_for_status()
        token = login.json()["access_token"]

        # Selecting the model is a user preference, so a run is reproducible
        # only if it is set explicitly rather than inherited.
        pick = await http.put(f"{args.base_url}/capi/assist/models",
                              headers={"Authorization": f"Bearer {token}"},
                              json={"model_id": args.model})
        if pick.status_code >= 400:
            print(f"could not select {args.model}: HTTP {pick.status_code} "
                  f"{pick.text[:200]}", file=sys.stderr)
            return 3

        started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
        loop = Loop(http, args.base_url, token)
        # Studio projects accumulate across runs in the shared environment,
        # and the model finds the previous run's by name and reuses it. That
        # is realistic, but it makes "what did THIS run build" unanswerable
        # from the final list -- so take the before-picture and diff.
        pre_existing = {p.get("id") for p in await loop.studio_projects()}
        report_id = await loop.create_article(f"EU spending in Russian companies {started}")
        key = f"report:{report_id}"
        print(f"article {report_id}, model {args.model}, asking...",
              file=sys.stderr)

        await loop.turn(report_id, prompt, key)
        for _ in range(max(0, args.turns - 1)):
            await loop.turn(report_id, "Please continue.", key)
        # Apply between turns, not only at the end: the second turn is
        # supposed to EDIT what the first wrote, and it reads the saved
        # document. Leaving the draft unapplied would hand it a blank
        # article and make a focused edit impossible to even attempt.
        applied = await apply_proposals(loop, report_id)
        for follow_up in args.then:
            loop.turn_boundaries.append(len(loop.calls))
            await loop.turn(report_id, follow_up, key)
            applied += await apply_proposals(loop, report_id)

        article = await loop.read_document(report_id)
        projects = await loop.studio_projects()

    meta = {"started": started, "model": args.model,
            "base_url": args.base_url, "report_id": report_id,
            "turns": args.turns}
    text = report(loop, meta, applied, article, projects, pre_existing)
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"story-loop-{started}-{args.model}.md"
    path.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nwritten to {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
