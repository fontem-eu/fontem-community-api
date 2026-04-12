"""HTML sanitization for user-generated content.

Uses nh3 (Rust-based, memory-safe) to strip dangerous HTML while
preserving the subset that TipTap's StarterKit can produce.
"""
from __future__ import annotations

import nh3

# Tags that TipTap's StarterKit extension legitimately produces.
# Anything else (script, iframe, object, embed, form, etc.) is stripped.
ALLOWED_TAGS = {
    # Block
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "code",
    "ul", "ol", "li",
    # Inline
    "strong", "b", "em", "i", "u", "s", "del",
    "a", "span", "sub", "sup", "mark",
    # Media (stripped of event handlers)
    "img",
    # Table
    "table", "thead", "tbody", "tr", "th", "td",
}

# Attributes allowed per tag.  Event handlers (on*) are always stripped by nh3.
ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title", "target"},  # rel managed by link_rel param
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "span": {"class"},
    "code": {"class"},
    "pre": {"class"},
}

# URL schemes allowed in href/src.  javascript: is blocked.
ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


def sanitize_html(html: str) -> str:
    """Sanitize HTML, keeping only safe tags and attributes."""
    if not html:
        return html
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
    )


def sanitize_text(text: str) -> str:
    """Strip ALL HTML from a plain-text field (title, abstract)."""
    if not text:
        return text
    return nh3.clean(text, tags=set())
