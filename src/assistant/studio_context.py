"""What the assistant is told about the Data Studio the user is looking at.

The Studio tools act on saved projects by id, and until now that was the
whole of the assistant's view: it could list, read and write, but it could
not see the query the user had OPEN — the draft in the editor, which is
not the saved text the moment they start typing. Asked to "fix this", it
had to guess which query "this" was.

The client sends a snapshot with each turn (router.py `StudioContextBody`)
and this module renders it into one prompt section. Per turn, because the
draft changes per turn: it sits with the volatile sections of the system
prompt rather than the cached prefix.

The labels here are load-bearing. The scripted e2e model (mock_llm.py)
reads the ids back out of this block with regexes on `project_id:`,
`query_id:` and `language:`, so a real turn and a scripted one see the
same text. Change a label here and change the mock with it.
"""
from __future__ import annotations

#: How much of the open query goes into the prompt. The body cap is 8000
#: characters; the prompt carries half of that, because a query that long
#: is the exception and the whole block is re-prefilled every turn.
MAX_QUERY_CHARS = 4_000

_TRUNCATED = " …[truncated]"

_PROPOSAL_GUIDE = (
    "To change this query's text, call mcp__gmr__studio_propose_query with "
    "the COMPLETE new text and a one-sentence explanation. The user reviews "
    "it as a diff and accepts or rejects it as a whole — never assume it "
    "was applied; the next turn shows the current text. "
    "mcp__gmr__studio_update_query is refused for this query's text. To try "
    "a candidate before proposing it, use mcp__gmr__query_graph (read-only). "
    "The language is yours to choose, not fixed by the open query: it picks "
    "the store. Cypher for the Neo4j graph (companies, contracts, "
    "authorities, lobbyists), sql for the statistics, sparql for Virtuoso "
    "(legislation, Wikidata, anything only held as RDF). Pass `lang` when "
    "the question needs a different store than the open query uses."
)


def open_query(payload_or_studio: dict | None) -> dict | None:
    """The {project_id, query_id, lang} of the query open in the editor.

    Accepts either the turn payload's `studio_editor` (already that shape)
    or the raw `studio` body, so callers on both sides of the service
    boundary ask one question. None when nothing is open.
    """
    if not isinstance(payload_or_studio, dict):
        return None
    if "query_id" in payload_or_studio:
        editor = payload_or_studio
    else:
        query = payload_or_studio.get("query")
        if not isinstance(query, dict):
            return None
        editor = {
            "project_id": payload_or_studio.get("project_id"),
            "query_id": query.get("id"),
            "lang": query.get("lang"),
        }
    project_id = str(editor.get("project_id") or "").strip()
    query_id = str(editor.get("query_id") or "").strip()
    if not project_id or not query_id:
        return None
    return {
        "project_id": project_id,
        "query_id": query_id,
        "lang": str(editor.get("lang") or "").strip().lower(),
    }


def _capped(text: str) -> str:
    if len(text) <= MAX_QUERY_CHARS:
        return text
    return text[:MAX_QUERY_CHARS] + _TRUNCATED


def system_context(studio: dict | None) -> str:
    """The `## Data Studio` section, or '' when the user is not in one."""
    if not studio:
        return ""
    lines = [
        "## Data Studio",
        "",
        f'The user is working in the Data Studio project '
        f'"{studio.get("project_name") or ""}" '
        f'(project_id: {studio.get("project_id") or ""}).',
    ]
    query = studio.get("query")
    if not isinstance(query, dict):
        lines.append("No query is open.")
        return "\n".join(lines)

    lines.append(
        f'Open query: "{query.get("name") or ""}" '
        f'(query_id: {query.get("id") or ""}), '
        f'language: {query.get("lang") or ""}.')
    lines += ["Current text:", "```", _capped(str(query.get("text") or "")), "```"]
    # Both optional, and omitted rather than rendered empty: "Last run
    # error: None" reads as a fact about the query.
    if query.get("last_error"):
        lines.append(f"Last run error: {query['last_error']}")
    columns = query.get("columns")
    if columns:
        lines.append("Result columns: " + ", ".join(str(c) for c in columns))
    lines += ["", _PROPOSAL_GUIDE]
    return "\n".join(lines)
