"""Constrained scalar types for text a person types.

A label is one line somebody reads: a name, a title. It is never markup.
Nothing in this API renders a label as HTML — the SPA binds labels with
``{{ }}`` and every ``v-html`` it has goes through DOMPurify — but labels are
echoed back verbatim in JSON list responses, and a field constrained only by
``max_length`` will store ``<script>alert(1);</script>`` and re-serve it for
as long as the row exists.

That is what a DAST scanner reports as "Cross Site Scripting Weakness
(Persistent in JSON Response)", and it is a fair complaint about the data even
where the rendering happens to be safe: it leaves the API one careless
``v-html`` away from a stored XSS, and markup is simply not what a name is.
On the 2026-09-25 scan the DAST database held 353 stored tag-bearing labels
across dossiers, investigations and studio projects, every one of them put
there through this API by a scanner.

The rule is deliberately narrow, because these fields carry 24 languages and
somebody's real title is not ours to mangle:

* A bare ``<`` or ``>`` is fine, and so is ``revenue<cost``. A tag has to
  close to count as one, so only ``<name ...>``, ``</name>``, ``<!-- -->``,
  ``<![CDATA[`` and ``<?...?>`` are rejected.
* Every letter, accent, quote and dash passes untouched. There is no
  allow-list of characters and no transliteration.
* C0/C1 control characters are rejected outright. No label has ever needed
  one and they corrupt logs, CSV exports and terminals downstream.

Free text is deliberately NOT covered. A saved query may say ``a<b``, and a
story abstract may discuss a ``<script>`` tag; those fields are prose or code,
they are rendered through DOMPurify or not rendered as HTML at all, and
rejecting markup there would be wrong. The control for those is output
encoding, which is already in place.
"""
from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import AfterValidator

# A tag has to CLOSE to be a tag. Requiring the ``>`` is what lets
# "Spending < 2020" and "revenue<cost" through while still catching
# ``<script>``, ``<img src=x>`` and ``</b>`` — an earlier version of this rule
# only looked for ``<`` followed by a letter and turned away "x<y".
_TAG_LIKE = re.compile(r"<\s*/?[a-zA-Z][^<>]*>|<!--|<!\[|<\?")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def _check(value: str, what: str) -> None:
    if _CONTROL.search(value):
        raise ValueError(f"{what} must not contain control characters")
    if _TAG_LIKE.search(value):
        raise ValueError(f"{what} must not contain HTML or XML tags")


def _label(value: str) -> str:
    """Validate and trim one line of human-readable text."""
    _check(value, "a name")
    trimmed = value.strip()
    if not trimmed and value:
        raise ValueError("a name must contain more than whitespace")
    return trimmed


def _spec(value: dict | None) -> dict | None:
    """Reject markup anywhere inside a free-form spec document.

    A plot spec is column names, a chart type and axis configuration — the
    keys the Studio writes are ``chart``, ``x``, ``y``, ``y2``, ``level``,
    ``transform``, ``series``, ``corrCols``, ``bivariate``, ``events``,
    ``sources``, ``value``, ``requiredParams`` and ``oneOfParams``. None of
    them is markup, so the check does not need to know the vocabulary and
    keeps working when the Studio grows a new key.

    This is also a correctness guard. ``spec`` is an unvalidated ``dict``, so
    ``chart: "<script>alert(1);</script>"`` was not only stored, it was stored
    as a plot that can never render.
    """
    if value is None:
        return None

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                walk(item, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for item in node:
                walk(item, path)
        elif isinstance(node, str):
            _check(node, f"spec field {path!r}" if path else "the spec")

    walk(value, "")
    return value


Label = Annotated[str, AfterValidator(_label)]
SpecDocument = Annotated[dict, AfterValidator(_spec)]
