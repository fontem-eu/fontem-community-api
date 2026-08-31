"""Block-level differences between two article documents.

Two revisions are two TipTap documents. What a reviewer needs to see is
not a character diff of serialised JSON — it is which paragraphs,
headings and widgets were added, removed or rewritten.

So a document is flattened to its top-level blocks and the two lists are
matched. Matching is on the block's *content*, not its position, so
inserting a paragraph at the top does not report every block below it as
changed — the failure that makes a naïve diff useless to read.

Pure functions, no I/O: the same code answers a review screen, a history
comparison, and (later) a merge request's conflict view.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

#: Node types the review screen can render for real rather than
#: describe. Everything else is text, and text is its own description.
_RENDERABLE_ATOMS = ("widget", "image")

#: Blocks longer than this are compared by a prefix. A block is a
#: paragraph, not a book, and an unbounded key would make matching cost
#: grow with document size for no gain in accuracy.
_KEY_CHARS = 400


def _text_of(node: dict) -> str:
    """Every character a reader would see in this node."""
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return str(node.get("text") or "")
    parts = [_text_of(child) for child in node.get("content") or []]
    return "".join(parts)


def _label(node: dict) -> str:
    """What this block is, for a reader of the diff.

    Atoms carry no text, so they describe themselves: a widget that
    changed which entity it points at has changed, and a diff that showed
    two empty blocks would hide it.
    """
    kind = str(node.get("type") or "block")
    attrs = node.get("attrs") or {}
    if kind == "heading":
        return f"heading{attrs.get('level') or ''}"
    if kind == "widget":
        widget = attrs.get("widget_type") or "widget"
        entity = attrs.get("entityId") or ""
        return f"widget:{widget}:{entity}"
    if kind == "image":
        return f"image:{attrs.get('src') or ''}"
    return kind


def blocks(document: dict | None) -> list[dict]:
    """The comparable blocks of a stored document.

    Accepts either the stored wrapper (``{"tiptap": ..., "version": 2}``)
    or a bare TipTap document, because callers hold both.
    """
    if not isinstance(document, dict):
        return []
    doc = document.get("tiptap") if "tiptap" in document else document
    if not isinstance(doc, dict):
        return []
    out: list[dict] = []
    for node in doc.get("content") or []:
        if not isinstance(node, dict):
            continue
        block = {
            "type": str(node.get("type") or "block"),
            "label": _label(node),
            "text": _text_of(node),
        }
        # Atoms carry no text, so a reviewer reading them as text reads
        # nothing. Their attributes ride along, which lets the review
        # screen render the actual widget instead of a description of
        # one — reviewing "widget:graph_explorer:00d87075" is not
        # reviewing the thing that will be published.
        attrs = node.get("attrs")
        if block["type"] in _RENDERABLE_ATOMS and isinstance(attrs, dict):
            block["attrs"] = attrs
        out.append(block)
    return out


def _key(block: dict) -> str:
    return f"{block['label']}\x00{block['text'][:_KEY_CHARS]}"


def diff(before: dict | None, after: dict | None) -> list[dict]:
    """Block operations turning ``before`` into ``after``.

    Each entry is ``{"op", "before", "after"}`` where op is one of
    equal / insert / delete / replace. A replace pairs the blocks it
    replaced so the UI can show them side by side; unpaired blocks in a
    replace run appear as inserts or deletes rather than being dropped.
    """
    old, new = blocks(before), blocks(after)
    matcher = SequenceMatcher(
        a=[_key(b) for b in old], b=[_key(b) for b in new], autojunk=False)

    out: list[dict] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            out.extend({"op": "equal", "before": old[i], "after": new[j]}
                       for i, j in zip(range(i1, i2), range(j1, j2)))
        elif op == "insert":
            out.extend({"op": "insert", "before": None, "after": new[j]}
                       for j in range(j1, j2))
        elif op == "delete":
            out.extend({"op": "delete", "before": old[i], "after": None}
                       for i in range(i1, i2))
        else:  # replace
            paired = min(i2 - i1, j2 - j1)
            out.extend({"op": "replace", "before": old[i1 + n],
                        "after": new[j1 + n]} for n in range(paired))
            out.extend({"op": "delete", "before": old[i], "after": None}
                       for i in range(i1 + paired, i2))
            out.extend({"op": "insert", "before": None, "after": new[j]}
                       for j in range(j1 + paired, j2))
    return out


def summary(operations: list[dict]) -> dict[str, Any]:
    """Counts, for a history row that has to fit on one line."""
    counts = {"added": 0, "removed": 0, "changed": 0}
    for entry in operations:
        if entry["op"] == "insert":
            counts["added"] += 1
        elif entry["op"] == "delete":
            counts["removed"] += 1
        elif entry["op"] == "replace":
            counts["changed"] += 1
    return counts
