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

import re
from typing import Any

#: What joins two blocks in the coordinate space. A blank line, because
#: that is what a reader sees and what the model will count.
SEPARATOR = "\n\n"


#: How a chart appears in the text the model reads and writes.
#:
#: A widget node has no `content`, so it used to contribute NOTHING to
#: body_text: the model read its own article and saw a blank gap where its
#: chart was. Then it rewrote the body as HTML -- which cannot express a
#: widget's data_params -- and the chart was silently dropped. It is not a
#: mistake the model could avoid; the chart did not exist in anything it
#: could see or say.
#:
#: The number is what resolves the marker back to a widget, and it is the
#: 1-based position of that widget in the document AS READ. That is enough,
#: because the only cycle that matters is one read followed by one rewrite.
#: The label after it is for the model's benefit and is never parsed --
#: nothing else in a stored widget names it (there is no plot id and no
#: title in the node), so the source name it chose when building the plot
#: is the one human handle available.
#:
#: The model reached for exactly this on its own once, writing
#: `[[SECTOR_CHART]]` into a draft and then searching for it.
#: The number is optional. A model writing prose about a chart it is
#: inserting in the SAME turn has no number to quote -- the chart is not in
#: the saved document yet -- so `[[chart: <label>]]` resolves by label
#: instead. It reached for exactly that on its own, writing
#: `[[chart: €M by country]]` in a draft, and the numbered form was the only
#: one that worked.
#: No `\s*` before the label: `\s` is a subset of `[^\]]`, so the two
#: compete for the same leading spaces and a long run of them with no
#: closing bracket costs polynomial time to fail (SonarQube python:S5852).
#: The label is stripped in code instead, which it already was.
MARKER_RE = re.compile(r"\[\[chart ?(\d+)?(?::([^\]]*))?\]\]")


def label_for(block: dict) -> str:
    """What to call this chart, in the words most likely to identify it.

    Ordered by how much it actually distinguishes one chart from another:

    1. The plot's own name, recorded on the widget when it was inserted.
       "Number of EU contracts awarded to Israeli companies, by buyer
       country" cannot be mistaken for anything else.
    2. Its axes. `contracts by buyer_country` and
       `millions_eur by buyer_country` are different charts and read as
       different charts, which is the whole job.
    3. The source query's name -- the old behaviour, and the reason this
       function exists. A well-built project runs several charts off ONE
       base query, so every marker in a three-chart article read
       `[[chart N: il_contracts]]`. That labelled nothing.

    The cost of (3) was not merely a wasted word. Asked to place four
    charts, a model carried a belief from earlier in the conversation about
    which chart the article already held, read a marker that was compatible
    with every chart in the project, and took it as confirmation. It
    inserted a duplicate of the chart already there and never inserted the
    one it thought was there -- leaving prose that describes a chart the
    article does not contain. A label that names the axes would have
    contradicted it outright.
    """
    attrs = block.get("attrs") or {}
    title = attrs.get("title")
    if title:
        return str(title)
    ui = attrs.get("ui_params") or {}
    x, y = ui.get("x"), ui.get("y")
    if x and y:
        return f"{y} by {x}"
    sources = (attrs.get("data_params") or {}).get("sources") or []
    if sources and isinstance(sources[0], dict) and sources[0].get("name"):
        return str(sources[0]["name"])
    return str(attrs.get("widget_type") or "chart")


def marker_for(index: int, block: dict) -> str:
    """The text that stands in for one widget, 1-based `index`."""
    return f"[[chart {index}: {label_for(block)}]]"


def _widget_markers(blocks: list[dict]) -> dict[int, str]:
    """Marker text for each block index that holds a widget."""
    out, seen = {}, 0
    for i, block in enumerate(blocks):
        if block.get("type") == "widget":
            seen += 1
            out[i] = marker_for(seen, block)
    return out


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
    """The document's top-level blocks, whatever wrapper it arrived in.

    Three shapes reach this, and missing the first one made every
    character-addressed tool answer as though the article were empty:

      {"tiptap": {...}, "version": 2}   what save_document STORES
      {"type": "doc", "content": [...]} the TipTap document itself
      [block, block, ...]               early drafts, a bare block list

    The stored shape is a wrapper, so reading `content` off it finds
    nothing. On a blank article that is indistinguishable from correct,
    which is why it survived the first run: only once the article had text
    did find_in_document start reporting "not in the body" for every
    substring, including "the", and replace_part refuse a span against a
    body it measured as 0 characters.
    """
    if isinstance(doc, list):
        return [b for b in doc if isinstance(b, dict)]
    if isinstance(doc, dict):
        inner = doc.get("tiptap")
        if isinstance(inner, (dict, list)):
            return _top_blocks(inner)
        content = doc.get("content")
        if isinstance(content, list):
            return [b for b in content if isinstance(b, dict)]
    return []


def _rendered(blocks: list[dict]) -> list[str]:
    """Each block as it appears in body_text -- widgets as their marker."""
    markers = _widget_markers(blocks)
    return [markers.get(i) or _text_of(b) for i, b in enumerate(blocks)]


def block_spans(doc: Any) -> list[tuple[int, int]]:
    """(start, end) of each top-level block, in body_text coordinates."""
    spans, cursor = [], 0
    for i, text in enumerate(_rendered(_top_blocks(doc))):
        if i:
            cursor += len(SEPARATOR)
        spans.append((cursor, cursor + len(text)))
        cursor += len(text)
    return spans


def body_text(doc: Any) -> str:
    """The article as the model addresses it, charts included.

    A chart is a `[[chart N: label]]` marker rather than a blank gap, so
    the model can see where its charts are, keep them through a rewrite,
    move them, or delete one on purpose. See MARKER_RE.
    """
    return SEPARATOR.join(_rendered(_top_blocks(doc)))


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


def _chart_cut_by(blocks: list[dict], spans: list[tuple[int, int]],
                  touched: list[int], start: int, end: int) -> dict | None:
    """Refuse a span that would splice half a chart marker.

    A chart is atomic. Its marker is the whole of its block's text, so a
    span starting or ending INSIDE one leaves `[[chart 1: by_c` in the
    article -- text that renders as nothing and restores as nothing. A span
    that CONTAINS the marker whole is allowed: "replace this passage, chart
    and all" is a thing the model may mean, and is how a chart is deleted.
    """
    for i in touched:
        if blocks[i].get("type") != "widget":
            continue
        block_start, block_end = spans[i]
        if start > block_start or end < block_end:
            return {"error": (
                f"span {start}..{end} cuts through the chart at "
                f"{block_start}..{block_end}. A chart is atomic: quote a "
                f"span that excludes it, or one that covers its whole "
                f"marker to replace the chart with what you send.")}
    return None


def _one_block_replaced(block: dict, span: tuple[int, int],
                        start: int, end: int, new_text: str) -> list[dict]:
    """The blocks that replace a single touched block.

    A widget here is a chart whose whole marker the span covers -- a
    partial cut was refused already -- so it becomes the replacement text,
    or nothing at all when that text is empty. Deleting a chart on purpose
    is a thing markers made expressible; before them there was no way to
    say it.
    """
    if block.get("type") == "widget":
        return _paragraphs(new_text)
    spliced = _splice_in_block(block, start - span[0], end - span[0], new_text)
    # A block emptied by the edit goes; leaving it renders a stray blank
    # paragraph the user then has to delete by hand.
    keep = _text_of(spliced) or spliced.get("type") != "paragraph"
    return [spliced] if keep else []


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

    cut = _chart_cut_by(blocks, spans, touched, start, end)
    if cut:
        return cut

    first, last = touched[0], touched[-1]
    if first == last:
        middle = _one_block_replaced(blocks[first], spans[first],
                                     start, end, new_text)
    else:
        middle = _paragraphs(new_text)
    return _as_doc(doc, blocks[:first] + middle + blocks[last + 1:])


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
    """Put rebuilt blocks back in the shape the document arrived in.

    A stored document must come back stored-shaped: handing the editor a
    bare {"type": "doc"} where it expected {"tiptap": ...} would apply an
    edit that silently replaced the whole wrapper.
    """
    if isinstance(original, dict) and isinstance(original.get("tiptap"), (dict, list)):
        return {**original, "tiptap": _as_doc(original["tiptap"], blocks)}
    if isinstance(original, dict) and original.get("type"):
        return {**original, "content": blocks}
    return {"type": "doc", "content": blocks}


def _normalised(text: str) -> str:
    """Collapse whitespace and case, for anchor matching.

    An anchor is a phrase the model quotes from prose it just wrote. By the
    time that prose is a document it has been through HTML and TipTap, and
    line breaks and runs of spaces do not survive that intact. Matching on
    the exact bytes would fail on a difference no reader could see.
    """
    return " ".join(text.split()).casefold()


def block_after_anchor(doc: Any, anchor: str) -> int | None:
    """Index just after the first block containing `anchor`, or None.

    This is the apply-time half of anchor placement. The model names a
    phrase instead of a character offset, because in the turn that writes
    the body there are no offsets to name: a proposal is a card awaiting
    the user, so `read_document` still returns the last SAVED text, and a
    model that has just proposed a whole article reads back "".

    Iteration 4 hit exactly that. It proposed the body, read the document,
    got body_text_length 0, and inserted four charts with no position --
    so an article whose prose says "Below: a direct comparison" ends with
    all four charts after the source line.

    None means "not found", and the caller appends: a chart at the end is
    a worse article, a lost chart is a worse bug.
    """
    if not anchor or not anchor.strip():
        return None
    needle = _normalised(anchor)
    for i, block in enumerate(_top_blocks(doc)):
        if needle in _normalised(_text_of(block)):
            return i + 1
    return None


def widgets_of(doc: Any) -> list[dict]:
    """Every widget node in the document, in the order body_text numbers them."""
    return [b for b in _top_blocks(doc) if b.get("type") == "widget"]


def _split_on_markers(block: dict, widgets: list[dict]) -> list[dict]:
    """One incoming text block, with its markers turned back into widgets.

    A marker normally arrives as a paragraph of its own, because that is how
    body_text rendered it. It is split out of surrounding prose anyway: a
    model that writes the marker mid-sentence should get its chart, not a
    paragraph with `[[chart 1: ...]]` printed in it.

    A marker naming a chart that does not exist is dropped rather than left
    as literal text. It means the model asked for a chart the document has
    not got, and printing the brackets into a published article is the one
    outcome nobody wants.
    """
    text = _text_of(block)
    if not MARKER_RE.search(text):
        return [block]
    out: list[dict] = []
    cursor = 0
    for match in MARKER_RE.finditer(text):
        before = text[cursor:match.start()].strip()
        if before:
            out.append({"type": "paragraph",
                        "content": [{"type": "text", "text": before}]})
        widget = _widget_named(match, widgets)
        if widget is not None:
            out.append(widget)
        cursor = match.end()
    tail = text[cursor:].strip()
    if tail:
        out.append({"type": "paragraph", "content": [{"type": "text", "text": tail}]})
    return out


def _widget_named(match, widgets: list[dict]) -> dict | None:
    """The widget a marker refers to, by number or by label.

    The number is the position of that chart in the document as read, and
    it is what a model quotes back from body_text. The LABEL is what it has
    when the chart is not in the document yet -- it cannot count a chart it
    is inserting in the same turn, and it should not have to wait a round
    trip to describe one.

    Number first, because a numbered marker was read from a real document
    and says exactly which chart. Label second, matched the way anchors
    are, so spacing and case do not decide whether a chart survives.
    """
    number, label = match.group(1), (match.group(2) or "").strip()
    if number:
        index = int(number) - 1
        if 0 <= index < len(widgets):
            return widgets[index]
        # A number past the end is a chart this document has not got. Fall
        # through to the label rather than dropping outright: the model may
        # have numbered a chart it is inserting this turn.
    if not label:
        return None
    wanted = _normalised(label)
    for widget in widgets:
        if _normalised(label_for(widget)) == wanted:
            return widget
    return None


def restore_widgets(blocks: list[dict], previous: Any) -> list[dict]:
    """Put the charts back into a body the model rewrote as text.

    `replace_body` speaks HTML, and HTML cannot carry a widget's
    data_params -- so before markers existed, every whole-body rewrite
    silently deleted the charts already in the article. Iteration 6 lost
    one that way: three charts made, two in the article.

    The nodes come from the document being replaced, not from a re-fetch:
    the widget the marker names is right there, and reusing it verbatim
    keeps the chart identical rather than merely equivalent.

    A marker the model dropped stays dropped. That is the other half of
    what markers buy -- deleting a chart on purpose, which it could not
    express at all before.
    """
    # No early-out when `previous` holds no widgets. A marker that resolves
    # to nothing must still be REMOVED, and skipping the walk is how literal
    # `[[chart 1: ...]]` text reached a published article in iteration 7 --
    # printed brackets beside the chart they were meant to be. Losing a
    # chart is bad; rendering the plumbing is worse.
    widgets = widgets_of(previous)
    out: list[dict] = []
    for block in blocks:
        out.extend(_split_on_markers(block, widgets))
    return out
