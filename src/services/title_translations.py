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

A contract card's buyer (``facets.from``) is the same story with one more
step: the item keeps only the buyer's name, as text, and translations are
kept per authority. fontem-api finds the buyer from the contract key, picked
as the public-contracts query picks it, and answers with the name it has
stored; the card's name is swapped only when that is the name it shows, and
the original stays as ``facets.from_original``.
"""
from __future__ import annotations

import copy
import os
import time
from functools import lru_cache
from typing import Any, Protocol

import httpx
from loguru import logger

from src.domain.feed import FeedItem
from src.services.query_executor import DEFAULT_BASE_URL

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
                     cohesion_ids: list[str],
                     buyer_keys: list[str] = ()) -> dict[str, dict[str, Any]]:
        """``{"contracts": {key: title}, "cohesion": {id: title}, "buyers":
        {contract_key: (name, stored name)}}``, only for what has a
        translation in ``lang``."""


class HttpTitleTranslator:
    """Asks fontem-api, through the same internal address the query proxies
    use, and remembers the answers for a while."""

    def __init__(self, base_url: str | None = None, *, ttl: float = CACHE_TTL_S,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        # The same in-cluster address the query proxies are reached at.
        self._base = (base_url or os.environ.get("GMR_API_INTERNAL", DEFAULT_BASE_URL)).rstrip("/")
        self._ttl = ttl
        self._transport = transport  # tests only
        # (lang, bucket, key) -> (expires_at, what was found or None): a
        # title, or for a buyer its (name, stored name).
        self._cache: dict[tuple[str, str, str], tuple[float, Any]] = {}

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
                  found: dict[str, Any]) -> None:
        if len(self._cache) > CACHE_MAX:
            self._cache.clear()
        expires = time.monotonic() + self._ttl
        for key in asked:
            self._cache[(lang, bucket, key)] = (expires, found.get(key))

    async def _fetch(self, lang: str, contract_keys: list[str],
                     cohesion_ids: list[str],
                     buyer_keys: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {"contracts": {}, "cohesion": {}, "buyers": {}}
        longest = max(len(contract_keys), len(cohesion_ids), len(buyer_keys))
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=self._transport) as client:
            for start in range(0, longest, MAX_KEYS):
                resp = await client.post(f"{self._base}/translations/titles", json={
                    "lang": lang,
                    "contract_keys": contract_keys[start:start + MAX_KEYS],
                    "cohesion_ids": cohesion_ids[start:start + MAX_KEYS],
                    "buyer_contract_keys": buyer_keys[start:start + MAX_KEYS],
                })
                resp.raise_for_status()
                body = resp.json()
                for bucket in ("contracts", "cohesion"):
                    for key, hit in (body.get(bucket) or {}).items():
                        if isinstance(hit, dict) and hit.get("title"):
                            out[bucket][key] = hit["title"]
                # A buyer keeps the stored name beside the translation:
                # it is what decides whether the card shows this buyer.
                for key, hit in (body.get("buyers") or {}).items():
                    if isinstance(hit, dict) and hit.get("title") and hit.get("original"):
                        out["buyers"][key] = (hit["title"], hit["original"])
        return out

    async def lookup(self, lang: str, contract_keys: list[str],
                     cohesion_ids: list[str],
                     buyer_keys: list[str] = ()) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        asked = {"contracts": contract_keys, "cohesion": cohesion_ids,
                 "buyers": list(buyer_keys)}
        found, misses = {}, {}
        for bucket, keys in asked.items():
            found[bucket], misses[bucket] = self._cached(lang, bucket, keys, now)
        if any(misses.values()):
            try:
                fetched = await self._fetch(lang, misses["contracts"], misses["cohesion"],
                                            misses["buyers"])
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("title translations unavailable ({}): {}", lang, exc)
                return found
            for bucket in asked:
                self._remember(lang, bucket, misses[bucket], fetched[bucket])
                found[bucket].update(fetched[bucket])
        return found


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


def _localised(item: FeedItem, bucket: str, translated: str | None) -> FeedItem:
    """One item with its headline swapped for ``translated``, or the item
    itself when there is nothing to swap."""
    facets = item.facets or {}
    original = facets.get("headline") or item.summary
    if not translated or translated == original:
        return item
    out = copy.copy(item)
    # A contract's summary is its title too; a grant's is its programme.
    if bucket == "contracts" and item.summary == original:
        out.summary = translated
    out.facets = {**facets, "headline": translated, "headline_original": original}
    return out


def _buyer_key(item: FeedItem) -> str | None:
    """The contract key a card's buyer is looked up by, or None when the
    item names no buyer (grants, lobbying, a contract without a buyer)."""
    facets = item.facets or {}
    if facets.get("kind") == "contract" and facets.get("from") \
            and not item.item_id.startswith(COHESION_PREFIX):
        return item.item_id
    return None


def _with_buyer(item: FeedItem, buyer: tuple[str, str] | None) -> FeedItem:
    """The item with its buyer named in the reader's language — only when
    the buyer looked up carries the name the card shows."""
    facets = item.facets or {}
    if not buyer:
        return item
    name, original = buyer
    if facets.get("from") != original or name == original:
        return item
    out = copy.copy(item)
    out.facets = {**facets, "from": name, "from_original": original}
    return out


async def localise(items: list[FeedItem], lang: str | None,
                   translator: TitleTranslator | None) -> list[FeedItem]:
    """The items with their headline in ``lang`` where translated.

    Returns new items; the ones passed in are never changed. A contract's
    ``summary`` is its title too and follows the headline; a grant's is its
    programme and stays. ``title`` (the sentence an Atom reader shows) is
    left alone. A contract's buyer is swapped too, keeping the original as
    ``facets.from_original``.
    """
    if not lang or translator is None or not items:
        return items
    keys = [_key(item) for item in items]
    buyer_keys = [_buyer_key(item) for item in items]
    wanted: dict[str, list[str]] = {"contracts": [], "cohesion": []}
    for key in filter(None, keys):
        wanted[key[0]].append(key[1])
    buyers = [key for key in buyer_keys if key]
    if not any(wanted.values()) and not buyers:
        return items
    found = await translator.lookup(lang, wanted["contracts"], wanted["cohesion"], buyers)
    out = []
    for item, key, buyer_key in zip(items, keys, buyer_keys):
        if key:
            item = _localised(item, key[0], found.get(key[0], {}).get(key[1]))
        if buyer_key:
            item = _with_buyer(item, found.get("buyers", {}).get(buyer_key))
        out.append(item)
    return out
