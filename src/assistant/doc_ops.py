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

from src.assistant import doc_edit, tool_budget

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

    async def content(self):
        """The stored TipTap document itself, for the editing verbs.

        `read` returns the model-facing JSON string; this returns the
        structure the character-addressed edits are computed against, so
        the offsets a model got from one are the offsets the other
        applies. Returns None when the document cannot be read at all,
        which the caller reports rather than treating as empty — an edit
        against a document we could not load is an edit against nothing.
        """
        try:
            head = (await self._svc.draft_head(self._user, self._report)
                    or await self._svc.document_head(self._report))
        except Exception:  # pylint: disable=broad-except
            return None
        return head.content_json if head else []

    async def read(self) -> str:
        """Title, abstract and body of the conversation's report.

        Access control is the report service's: the same STORIES_READ check
        the user's own page load goes through, so the agent can read exactly
        what its user can and nothing else.
        """
        try:
            report = await self._svc.get(self._user, self._report)
            # The user's own draft, not the published text: that is the
            # document they have open and the one an edit lands on. An
            # assistant revising the published version while its user
            # edits a draft proposes changes against text neither of them
            # is looking at.
            head = (await self._svc.draft_head(self._user, self._report)
                    or await self._svc.document_head(self._report))
        except Exception as exc:  # pylint: disable=broad-except
            # Whatever the service refused (missing, not yours, deleted),
            # the model gets a reason it can act on rather than a stack.
            return json.dumps({"error": f"cannot read this document: {exc}"})

        # An article with nothing saved is EMPTY, not unreadable. Writing
        # its first draft is a normal thing to be asked for, and a
        # rewrite of nothing destroys nothing — the blind-rewrite guard
        # exists for documents that cannot be read, not for blank ones.
        content = head.content_json if head else []
        # The coordinate space for find_in_document, replace_part and the
        # at_char on the insert verbs. Sent alongside the JSON rather than
        # instead of it: the JSON is what carries widgets and marks, and
        # the text is what the model can count characters in.
        text = doc_edit.body_text(content)
        body = json.dumps(content)
        if len(body) > MAX_DOC_CHARS:
            dropped = len(body) - MAX_DOC_CHARS
            body = body[:MAX_DOC_CHARS] + TRUNCATED_MARKER.format(
                dropped=dropped, total=len(body) + dropped)

        return json.dumps({
            "report_id": self._report,
            "title": report.title,
            "abstract": getattr(report, "abstract", None),
            # TipTap document JSON. Text lives in the `text` fields;
            # propose whole-body replacements as HTML, which the Apply
            # path sanitises and converts. `body_text` below is the same
            # prose as one string, and is the ONLY thing character
            # offsets refer to — find_in_document returns offsets into
            # it, replace_part and at_char consume them.
            "sections": body,
            "body_text": text,
            "body_text_length": len(text),
            "revision": head.id if head else None,
            # Every edit verb PROPOSES; the user accepts the card in
            # their editor. So the text a model just wrote is legitimately
            # absent from here, and saying only "no saved text yet" invites
            # the reading that the write failed. One run proposed a whole
            # body, searched it for a marker it had just written, got
            # "not in the body", re-read the document, found it still
            # empty -- and proposed the entire body a second time.
            "note": (
                ("This is the user's last SAVED draft. Their editor buffer "
                 "may contain newer unsaved text."
                 if head else
                 "This article has no saved text yet — anything proposed "
                 "here is its first draft.")
                + " Edits you propose do NOT appear here: they are shown "
                  "to the user as cards to accept, and this endpoint "
                  "returns only saved text. Do not re-propose something "
                  "you have already proposed this turn — it is pending, "
                  "not lost."
            ),
        })
