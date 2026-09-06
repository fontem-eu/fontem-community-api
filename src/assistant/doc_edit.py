"""Character-addressed edits to the article body.

`replace_body` is the only way to change prose, and it costs the model the
WHOLE article to change one sentence. On a long data story that is most of
a turn's output budget spent restating text nobody asked to change, and
every restatement is a chance to drop a paragraph the user wrote.

So: address a span and replace it. The tools are deliberately few --
`find_in_document` to locate, `replace_part` to change -- because the model
has to be able to hold the whole scheme in its head while it works.

## The coordinate space

Offsets are over the body's PLAIN TEXT: every block's text, joined by a
blank line, exactly what `read_document` returns as `body_text`.

Not the HTML, though `replace_body` speaks HTML and that would have been
the tidier symmetry. WidgetNode.renderHTML (fontem-web
src/extensions/WidgetNode.js) emits only `data-widget-type` and
`data-entity-id`: a Studio `pipeline` widget's `data_params`/`ui_params`
are objects and do not survive the round trip. Splicing HTML would
silently drop the charts -- which is precisely what the data stories this
exists for are made of.

Not the TipTap JSON either: a model splicing JSON by character index
produces invalid JSON, and the failure is a corrupt document rather than
a refusal it can read and retry.

Widgets occupy a block but contribute no text, so a chart between two
paragraphs is one separator's worth of offsets, not zero -- the model can
address "after the chart" without the offsets sliding.

## What a span may cross

Within one block, the splice is exact: character `start` to `end` of that
block's text, marks on the surrounding text preserved.

Across blocks, it is block-granular: every block the span touches is
replaced by paragraphs built from `new_text` (split on blank lines). A
character-exact cross-block splice would have to decide what happens to
half a heading, and every answer to that is a surprise. Block granularity
is the honest reading of "replace this passage".
"""
from __future__ import annotations

from typing import Any

#: What joins two blocks in the coordinate space. A blank line, because
#: that is what a reader sees and what the model will count.
SEPARATOR = "\n\n"


def _text_of(node: Any) -> str:
    """Every character a reader would see in this node.

    Same walk as services/doc_diff._text_of. Duplicated rather than
    imported: doc_diff is a review-screen concern and this is a tool
    surface, and a shared helper across those two would be a dependency
    neither of them wants for six lines.
    """
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return str(node.get("text") or "")
    return "".join(_text_of(child) for child in node.get("content") or [])


def _top_blocks(doc: Any) -> list[dict]:
    """The document's top-level blocks, whatever wrapper it arrived in."""
    if isinstance(doc, list):
        return [b for b in doc if isinstance(b, dict)]
    if isinstance(doc, dict):
        content = doc.get("content")
        if isinstance(content, list):
            return [b for b in content if isinstance(b, dict)]
    return []


def block_spans(doc: Any) -> list[tuple[int, int]]:
    """(start, end) of each top-level block, in body_text coordinates."""
    spans, cursor = [], 0
    for i, block in enumerate(_top_blocks(doc)):
        if i:
            cursor += len(SEPARATOR)
        text = _text_of(block)
        spans.append((cursor, cursor + len(text)))
        cursor += len(text)
    return spans


def body_text(doc: Any) -> str:
    """The article as the model addresses it."""
    return SEPARATOR.join(_text_of(b) for b in _top_blocks(doc))


def find(doc: Any, needle: str) -> dict:
    """Where `needle` first occurs, and how many times it occurs at all.

    The count is the point. A model that gets start/end for a string
    appearing four times will edit the first one and believe it edited
    the one it meant; saying so lets it choose a longer, unique anchor.
    """
    if not needle:
        return {"error": "substring is required and was empty"}
    text = body_text(doc)
    start = text.find(needle)
    if start < 0:
        return {"found": False, "count": 0,
                "note": "not in the body. Check read_document -- the text "
                        "you are quoting may be from an earlier draft."}
    return {
        "found": True,
        "start": start,
        "end": start + len(needle),
        "count": text.count(needle),
    }


def _paragraphs(text: str) -> list[dict]:
    """new_text as TipTap blocks. Blank lines separate paragraphs."""
    out = []
    for chunk in text.split(SEPARATOR):
        stripped = chunk.strip("\n")
        if not stripped:
            continue
        out.append({"type": "paragraph",
                    "content": [{"type": "text", "text": stripped}]})
    return out


def _splice_in_block(block: dict, start: int, end: int, new_text: str) -> dict:
    """Replace start..end of this block's own text, keeping marks.

    Offsets are block-local. The replacement inherits the marks of the
    text node it starts in, so replacing three words inside a bold run
    stays bold instead of silently losing the emphasis.

    The three surviving pieces of a cut node — what came before the span,
    the replacement, what came after — are appended through one helper
    rather than three conditionals, because the conditionals were the
    whole of this function's complexity and none of them said anything.
    """
    out: list[dict] = []
    cursor = 0
    #: Consumed by the first node the span cuts, so the replacement lands
    #: once even when the span covers several text nodes.
    pending = new_text

    def keep(node: dict, text: str) -> None:
        if text:
            out.append({**node, "text": text})

    for child in block.get("content") or []:
        if child.get("type") != "text":
            out.append(child)
            continue
        text = str(child.get("text") or "")
        node_start, cursor = cursor, cursor + len(text)
        if cursor <= start or node_start >= end:
            out.append(child)                       # untouched by the span
            continue
        keep(child, text[:max(0, start - node_start)])
        keep(child, pending)
        pending = ""
        keep(child, text[max(0, end - node_start):])

    # A span that touched no text node at all — an empty paragraph, say.
    if pending:
        out.append({"type": "text", "text": pending})
    return {**block, "content": out}


def replace_span(doc: Any, start: int, end: int, new_text: str) -> dict:
    """The document with start..end of body_text replaced.

    Returns {"error": ...} rather than raising: every caller here is a
    tool, and a model can act on a sentence but not on a traceback.
    """
    if start < 0 or end < start:
        return {"error": f"invalid span {start}..{end}"}
    text = body_text(doc)
    if end > len(text):
        return {"error": f"span {start}..{end} runs past the body, which is "
                         f"{len(text)} characters. Re-read the document."}

    blocks = _top_blocks(doc)
    spans = block_spans(doc)
    touched = [i for i, (s, e) in enumerate(spans)
               # A zero-length block (a widget) is touched only by a span
               # that genuinely reaches it, not by one that merely ends
               # where it begins.
               if (s < end and e > start) or (s == e and start <= s <= end)]

    if not touched:
        # A span inside a separator: no block's text is affected. Insert
        # between the two blocks it sits between rather than refusing --
        # "add a paragraph here" is a reasonable thing to have meant.
        after = sum(1 for s, _ in spans if s <= start)
        rebuilt = blocks[:after] + _paragraphs(new_text) + blocks[after:]
        return _as_doc(doc, rebuilt)

    first, last = touched[0], touched[-1]
    if first == last:
        block_start = spans[first][0]
        spliced = _splice_in_block(
            blocks[first], start - block_start, end - block_start, new_text)
        # A block emptied by the edit goes; leaving it renders a stray
        # blank paragraph the user then has to delete by hand.
        keep = _text_of(spliced) or spliced.get("type") != "paragraph"
        rebuilt = (blocks[:first] + ([spliced] if keep else [])
                   + blocks[last + 1:])
        return _as_doc(doc, rebuilt)

    rebuilt = blocks[:first] + _paragraphs(new_text) + blocks[last + 1:]
    return _as_doc(doc, rebuilt)


def block_index_at(doc: Any, char: int) -> int:
    """Which block boundary `char` names, for inserting a widget.

    A widget is a block-level atom, so the only places it can go are
    between blocks. This answers with an INSERTION INDEX: 0 puts it
    before everything, len(blocks) after everything. A character inside a
    block inserts after that block, because "put the chart after this
    paragraph" is what someone pointing at a paragraph means.
    """
    spans = block_spans(doc)
    if not spans or char <= 0:
        return 0
    for i, (s, e) in enumerate(spans):
        if char <= s:
            return i
        if char <= e:
            return i + 1
    return len(spans)


def _as_doc(original: Any, blocks: list[dict]) -> dict:
    """Put rebuilt blocks back in the shape the document arrived in."""
    if isinstance(original, dict) and original.get("type"):
        return {**original, "content": blocks}
    return {"type": "doc", "content": blocks}
