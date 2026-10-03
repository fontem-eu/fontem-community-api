"""Briefing items in the reader's language.

A briefing item keeps its own copy of the contract's or grant's title,
taken when the feed last refreshed it. Machine translations of that title
arrive later and separately (fontem-translator writes ``title_<lang>`` on
the graph node, hours after the contract appears), and a refresh only
re-reads recent days, so a translation can never be relied on to reach the
stored item. It is looked up when the item is SHOWN instead: one batched
call to fontem-api's ``POST /translations/titles`` for the items on the
page, keyed by what the item already names — a contract's ``contract_key``
(its item_id) or a grant's disclosure id (``cohesion:<id>``).

The original stays on the item as ``facets.headline_original`` so the card
can always show what the source published. Nothing here can fail a page:
an unreachable graph API serves the items as they are.
"""
from __future__ import annotations

import os
import time
from dataclasses import replace
from functools import lru_cache
from typing import Protocol

import httpx
from loguru import logger

from src.domain.feed import FeedItem

#: The 24 EU official languages, the only ones translations exist in.
EU_LANGS = frozenset({
    "bg", "cs", "da", "de", "el", "en", "es", "et", "fi", "fr", "ga", "hr",
    "hu", "it", "lt", "lv", "mt", "nl", "pl", "pt", "ro", "sk", "sl", "sv",
})

COHESION_PREFIX = "cohesion:"
#: fontem-api accepts at most this many keys per list per request.
MAX_KEYS = 500
#: A page waits on this at most; past it the items are shown untranslated.
TIMEOUT_S = 3.0
#: How long a looked-up title (or the absence of one) is reused. Bounds how
#: late a fresh translation shows, and spares the graph the anonymous landing
#: feed, which every visitor loads with the same few hundred items.
CACHE_TTL_S = 600.0
CACHE_MAX = 50_000


def clean_lang(value: str | None) -> str | None:
    """A whitelisted two-letter EU code, or None (``de-AT`` -> ``de``)."""
    if not value or not isinstance(value, str):
        return None
    code = value.strip()[:2].lower()
    return code if code in EU_LANGS else None


class TitleTranslator(Protocol):
    async def lookup(self, lang: str, contract_keys: list[str],
                     cohesion_ids: list[str]) -> dict[str, dict[str, str]]:
        """``{"contracts": {key: title}, "cohesion": {id: title}}``, only
        for what has a translation in ``lang``."""


class HttpTitleTranslator:
    """Asks fontem-api, through the same internal address the query proxies
    use, and remembers the answers for a while."""

    def __init__(self, base_url: str | None = None, *, ttl: float = CACHE_TTL_S,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base = (base_url or os.environ.get("GMR_API_INTERNAL", "http://fontem-api")).rstrip("/")
        self._ttl = ttl
        self._transport = transport  # tests only
        # (lang, bucket, key) -> (expires_at, title or None)
        self._cache: dict[tuple[str, str, str], tuple[float, str | None]] = {}

    def _cached(self, lang: str, bucket: str, keys: list[str], now: float):
        hits, misses = {}, []
        for key in dict.fromkeys(keys):
            entry = self._cache.get((lang, bucket, key))
            if entry and entry[0] > now:
                if entry[1] is not None:
                    hits[key] = entry[1]
            else:
                misses.append(key)
        return hits, misses

    def _remember(self, lang: str, bucket: str, asked: list[str],
                  found: dict[str, str]) -> None:
        if len(self._cache) > CACHE_MAX:
            self._cache.clear()
        expires = time.monotonic() + self._ttl
        for key in asked:
            self._cache[(lang, bucket, key)] = (expires, found.get(key))

    async def _fetch(self, lang: str, contract_keys: list[str],
                     cohesion_ids: list[str]) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {"contracts": {}, "cohesion": {}}
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=self._transport) as client:
            for start in range(0, max(len(contract_keys), len(cohesion_ids)), MAX_KEYS):
                resp = await client.post(f"{self._base}/translations/titles", json={
                    "lang": lang,
                    "contract_keys": contract_keys[start:start + MAX_KEYS],
                    "cohesion_ids": cohesion_ids[start:start + MAX_KEYS],
                })
                resp.raise_for_status()
                body = resp.json()
                for bucket in ("contracts", "cohesion"):
                    for key, hit in (body.get(bucket) or {}).items():
                        if isinstance(hit, dict) and hit.get("title"):
                            out[bucket][key] = hit["title"]
        return out

    async def lookup(self, lang: str, contract_keys: list[str],
                     cohesion_ids: list[str]) -> dict[str, dict[str, str]]:
        now = time.monotonic()
        contracts, contract_misses = self._cached(lang, "contracts", contract_keys, now)
        grants, grant_misses = self._cached(lang, "cohesion", cohesion_ids, now)
        if contract_misses or grant_misses:
            try:
                fetched = await self._fetch(lang, contract_misses, grant_misses)
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("title translations unavailable ({}): {}", lang, exc)
                return {"contracts": contracts, "cohesion": grants}
            self._remember(lang, "contracts", contract_misses, fetched["contracts"])
            self._remember(lang, "cohesion", grant_misses, fetched["cohesion"])
            contracts.update(fetched["contracts"])
            grants.update(fetched["cohesion"])
        return {"contracts": contracts, "cohesion": grants}


@lru_cache(maxsize=1)
def shared_translator() -> HttpTitleTranslator:
    """The process's one translator. One, because it holds the cache of
    looked-up titles, which is the point of having it: the landing feed is
    the same few hundred items for every visitor."""
    return HttpTitleTranslator()


def _key(item: FeedItem) -> tuple[str, str] | None:
    """Which title an item shows: (bucket, key), or None for neither."""
    if item.item_id.startswith(COHESION_PREFIX):
        return "cohesion", item.item_id[len(COHESION_PREFIX):]
    if (item.facets or {}).get("kind") == "contract":
        return "contracts", item.item_id
    return None


async def localise(items: list[FeedItem], lang: str | None,
                   translator: TitleTranslator | None) -> list[FeedItem]:
    """The items with their headline in ``lang`` where translated.

    Returns new items; the ones passed in are never changed. A contract's
    ``summary`` is its title too and follows the headline; a grant's is its
    programme and stays. ``title`` (the sentence an Atom reader shows) is
    left alone.
    """
    if not lang or translator is None or not items:
        return items
    wanted: dict[str, list[str]] = {"contracts": [], "cohesion": []}
    for item in items:
        if (key := _key(item)) is not None:
            wanted[key[0]].append(key[1])
    if not any(wanted.values()):
        return items
    found = await translator.lookup(lang, wanted["contracts"], wanted["cohesion"])
    out = []
    for item in items:
        key = _key(item)
        translated = found.get(key[0], {}).get(key[1]) if key else None
        facets = item.facets or {}
        original = facets.get("headline") or item.summary
        if not translated or translated == original:
            out.append(item)
            continue
        summary = translated if key[0] == "contracts" and item.summary == original else item.summary
        out.append(replace(item, summary=summary, facets={
            **facets, "headline": translated, "headline_original": original}))
    return out
