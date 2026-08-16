"""Atom rendering for a watched briefing.

Atom rather than RSS 2.0: dates are RFC 3339 (so timezone handling is not
folklore), ``<id>`` is required rather than optional, and every element
declares its content type. RSS readers all consume Atom.

Everything here is escaped through ElementTree rather than string-formatted.
Feed titles carry authority and company names — apostrophes, ampersands,
non-Latin scripts — and one unescaped ``&`` makes a feed unparseable for
every reader at once.
"""
from __future__ import annotations

from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring

ATOM_NS = "http://www.w3.org/2005/Atom"
CONTENT_TYPE = "application/atom+xml; charset=utf-8"


def _rfc3339(value: datetime | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry_id(base_url: str, item) -> str:
    """A tag: URI built from the query and the item's own id.

    Readers use this to decide "have I shown this before", so it has to be
    stable for the life of the item and unique across briefings — which is
    exactly the guarantee item_id already carries.
    """
    host = base_url.split("://", 1)[-1].split("/", 1)[0] or "fontem.eu"
    return f"tag:{host},2026:feed-item/{item.query_id}/{item.item_id}"


def render(title: str, subtitle: str, feed_url: str, site_url: str, items) -> str:
    """Render a briefing's items as an Atom document."""
    feed = Element("feed", {"xmlns": ATOM_NS})
    SubElement(feed, "title").text = title
    if subtitle:
        SubElement(feed, "subtitle").text = subtitle
    SubElement(feed, "id").text = feed_url
    # rel=self is what a reader uses to re-find the feed after a redirect;
    # rel=alternate is the human page behind it.
    SubElement(feed, "link", {"rel": "self", "href": feed_url,
                              "type": "application/atom+xml"})
    SubElement(feed, "link", {"rel": "alternate", "href": site_url, "type": "text/html"})

    newest = max((i.item_time for i in items if i.item_time), default=None)
    SubElement(feed, "updated").text = _rfc3339(newest)
    author = SubElement(feed, "author")
    SubElement(author, "name").text = "Fontem"
    SubElement(author, "uri").text = site_url
    SubElement(feed, "generator", {"uri": site_url}).text = "Fontem Briefings"

    for item in items:
        entry = SubElement(feed, "entry")
        SubElement(entry, "title").text = item.title or "(untitled)"
        SubElement(entry, "id").text = _entry_id(site_url, item)
        if item.link:
            SubElement(entry, "link", {"rel": "alternate", "href": item.link,
                                       "type": "text/html"})
        SubElement(entry, "updated").text = _rfc3339(item.item_time)
        SubElement(entry, "published").text = _rfc3339(item.item_time)
        if item.summary:
            SubElement(entry, "summary", {"type": "text"}).text = item.summary
        # Regions as categories: a reader can filter on them, and it makes the
        # feed self-describing about why the item was included.
        for region in item.nuts or []:
            SubElement(entry, "category", {"term": region, "scheme": f"{site_url}/nuts"})

    return '<?xml version="1.0" encoding="utf-8"?>\n' + tostring(feed, encoding="unicode")


def etag_for(items) -> str:
    """A weak validator over the item ids, so a poll that changed nothing
    costs a 304 rather than a document."""
    if not items:
        return 'W/"empty"'
    newest = max((i.item_time for i in items if i.item_time), default=None)
    return f'W/"{len(items)}-{_rfc3339(newest)}-{items[0].item_id[:32]}"'
