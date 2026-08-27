"""Read the document the conversation is about, as the asking user.

The assistant was asked to "rewrite this draft" while nothing in its tool
surface could read a draft: `propose_edit` could only append, and the model
reasonably declined to revise a document it could not see. This is the
missing half — bound per turn to the user and the report the conversation
key names, so it can never read across users or across conversations.

Reads come from the server's stored version. The editor buffer in the
browser may be ahead of it by whatever the user has typed since the last
save; the tool description says so, because a model that treats this as the
live buffer will propose edits against a phantom baseline.
"""
from __future__ import annotations

import json

from src.assistant import tool_budget

#: One document read may not eat the whole turn's tool budget: the body is
#: the largest thing the tool surface can return, and a 90k-character story
#: would starve every later call. Same ceiling as any single tool result.
MAX_DOC_CHARS = tool_budget.MAX_TOOL_RESULT_CHARS

TRUNCATED_MARKER = (
    '\n\n[... document truncated: {dropped} of {total} characters omitted ...]'
)


class DocOps:
    """The document verbs, bound to (user, report) for one turn."""

    def __init__(self, report_service, user_id: str, report_id: str) -> None:
        self._svc = report_service
        self._user = user_id
        self._report = report_id

    async def read(self) -> str:
        """Title, abstract and body of the conversation's report.

        Access control is the report service's: the same STORIES_READ check
        the user's own page load goes through, so the agent can read exactly
        what its user can and nothing else.
        """
        try:
            report = await self._svc.get(self._user, self._report)
            sections = await self._svc.get_sections(self._report)
        except Exception as exc:  # pylint: disable=broad-except
            # Whatever the service refused (missing, not yours, deleted),
            # the model gets a reason it can act on rather than a stack.
            return json.dumps({"error": f"cannot read this document: {exc}"})

        body = json.dumps([s.content_json for s in sections])
        if len(body) > MAX_DOC_CHARS:
            dropped = len(body) - MAX_DOC_CHARS
            body = body[:MAX_DOC_CHARS] + TRUNCATED_MARKER.format(
                dropped=dropped, total=len(body) + dropped)

        return json.dumps({
            "report_id": self._report,
            "title": report.title,
            "abstract": getattr(report, "abstract", None),
            # TipTap document JSON, one entry per section. Text lives in
            # the `text` fields; propose replacements as HTML, which the
            # Apply path sanitises and converts.
            "sections": body,
            "note": (
                "This is the last SAVED version. The user's editor buffer "
                "may contain newer unsaved text."
            ),
        })
