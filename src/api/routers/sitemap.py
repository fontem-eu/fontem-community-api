"""Sitemap generation for crawlers.

Served at the web origin under ``/sitemap.xml``, proxied through nginx
from the community API. Split into a sitemap index + per-shard
sitemaps so we stay under the sitemaps.org limits (50k URLs or 50 MB
per file) without the frontend having to know our content catalog.

Shards:
  - core        — static routes (landing, feed, privacy, data-quality/*)
  - stories     — every public data story
  - (future)    — companies, authorities, lobbyists

Everything is rendered fresh on request. At current volume (thousands
of reports, not millions) that's well under the response budget.  If
it becomes a concern, the obvious caching spot is nginx — cache the
response with a short TTL keyed on the URL.
"""
# pylint: disable=missing-function-docstring
from __future__ import annotations

import os
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Response

from src.repositories.report_repository import ReportRepository

router = APIRouter(tags=["sitemap"])


# Canonical origin for absolute URLs in the sitemap. Must match the
# canonical the pages themselves emit (fontem-web's ssr.canonicalUrl) —
# a sitemap advertising one host while the pages name another is a
# contradictory signal to crawlers. Override per environment so dev
# emits dev URLs.
_CANONICAL_URL = os.environ.get("CANONICAL_URL", "https://dargle.eu")

# Routes that are public and worth indexing — anything served to an
# anonymous caller that returns real content.
#: Kept in step with sitemap_entities.COUNTRIES in fontem-api. Listing a
#: country here that the API does not know yields a 404 in the index,
#: which tests/test_sitemap.py guards against.
ENTITY_COUNTRIES: tuple[str, ...] = (
    "AUT", "BEL", "BGR", "HRV", "CYP", "CZE", "DNK", "EST", "FIN", "FRA",
    "DEU", "GRC", "HUN", "IRL", "ITA", "LVA", "LTU", "LUX", "MLT", "NLD",
    "POL", "PRT", "ROU", "SVK", "SVN", "ESP", "SWE", "ISL", "LIE", "NOR",
    "CHE", "GBR",
)

_CORE_ROUTES: list[tuple[str, str]] = [
    ("/", "daily"),
    ("/feed", "hourly"),
    ("/privacy", "yearly"),
    ("/sparql", "monthly"),
    ("/data-quality", "daily"),
    ("/data-quality/overview", "daily"),
    ("/data-quality/contracts", "daily"),
    ("/data-quality/gleif", "daily"),
    ("/data-quality/edgar", "daily"),
    ("/data-quality/esef", "daily"),
    ("/data-quality/lobbying", "daily"),
    ("/data-quality/trade-edges", "daily"),
    ("/data-quality/dedup", "weekly"),
    ("/data-quality/sanctions", "daily"),
    ("/data-quality/firds", "weekly"),
    ("/data-quality/cdp", "monthly"),
    ("/data-quality/nuts", "monthly"),
    ("/data-quality/eu-knowledge-graph", "weekly"),
]


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return datetime.now(timezone.utc).date().isoformat()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.date().isoformat()


def _xml_response(body: str) -> Response:
    """Sitemaps are cheap + stable-ish; let crawlers cache for an hour."""
    return Response(
        content=body,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap_index() -> Response:
    today = datetime.now(timezone.utc).date().isoformat()
    shards = [
        f"{_CANONICAL_URL}/sitemap-core.xml",
        f"{_CANONICAL_URL}/sitemap-stories.xml",
        # Listed companies, one shard per country. Served by fontem-api
        # (it owns the graph) and routed there by nginx; this index just
        # names them.
        #
        # Per country rather than one global file, because a global "top
        # N" buries the small member states — everything Maltese sits
        # below the German tail. Each country gets its own budget.
        #
        *(f"{_CANONICAL_URL}/sitemap-companies-{c}.xml" for c in ENTITY_COUNTRIES),
        # Authorities, now that /authority/:authority_id renders the full
        # entity page. They were held out while the SPA catch-all
        # answered 200 with a not-found view for those URLs: advertising
        # ~16,000 of them would have advertised ~16,000 soft-404s.
        *(f"{_CANONICAL_URL}/sitemap-authorities-{c}.xml" for c in ENTITY_COUNTRIES),
    ]
    items = "\n".join(
        f"  <sitemap><loc>{escape(u)}</loc><lastmod>{today}</lastmod></sitemap>"
        for u in shards
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{items}\n"
        "</sitemapindex>\n"
    )
    return _xml_response(xml)


@router.get("/sitemap-core.xml", include_in_schema=False)
def sitemap_core() -> Response:
    today = datetime.now(timezone.utc).date().isoformat()
    items = "\n".join(
        f"  <url><loc>{escape(_CANONICAL_URL)}{escape(path)}</loc>"
        f"<lastmod>{today}</lastmod><changefreq>{freq}</changefreq></url>"
        for path, freq in _CORE_ROUTES
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{items}\n"
        "</urlset>\n"
    )
    return _xml_response(xml)


@router.get("/sitemap-stories.xml", include_in_schema=False)
@inject
async def sitemap_stories(
    *, reports: FromDishka[ReportRepository],
) -> Response:
    # Use the existing list_public path; it already filters to
    # public_auth + public_open. We cap at 10k here — if the catalog
    # crosses that, split into paginated shards (sitemaps.org allows
    # up to 50k URLs per file).
    batch = await reports.list_public(limit=10_000, offset=0, authenticated=False)
    items = "\n".join(
        f"  <url><loc>{escape(_CANONICAL_URL)}/stories/{escape(r.id)}</loc>"
        f"<lastmod>{_iso(r.updated_at or r.created_at)}</lastmod>"
        "<changefreq>weekly</changefreq></url>"
        for r in batch
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{items}\n"
        "</urlset>\n"
    )
    return _xml_response(xml)
