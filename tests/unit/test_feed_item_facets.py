"""Facets: structured detail carried beside the prose title.

The card needs the parts of a finding — who, how much, to whom, and
whether the award carries integrity flags — with their own weight in the
layout. The query emits them; these tests cover the path from a query row
to the API payload, and the far more common case of an item that has none.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.domain.feed import FeedItem
from src.services import feed_contract
from src.services.feed_runner import _as_facets


class _Result:
    def __init__(self, columns, rows):
        self.columns = columns
        self.rows = rows


def _row(facets):
    return ["item-1", datetime(2026, 9, 11, tzinfo=timezone.utc), ["PT1B0"],
            34769555.3, "A awarded X to B", "https://example/c/1", "the title",
            facets]


COLS = ["item_id", "item_time", "nuts", "rank_value", "title", "link",
        "summary", "facets"]


class TestTheContract:
    def test_facets_is_optional_not_required(self):
        """A query written before facets existed must stay subscribable."""
        assert "facets" in feed_contract.OPTIONAL_COLUMNS
        assert "facets" not in feed_contract.REQUIRED_COLUMNS


class TestReadingTheColumn:
    def test_a_map_comes_through(self):
        f = _as_facets({"buyer": "Infraestruturas de Portugal", "red_flags": 1})
        assert f["buyer"] == "Infraestruturas de Portugal"
        assert f["red_flags"] == 1

    def test_nulls_inside_are_dropped(self):
        """A Cypher OPTIONAL MATCH that found nothing returns null, and a
        key whose value is null is noise the card would have to re-check."""
        assert _as_facets({"buyer": "X", "supplier": None}) == {"buyer": "X"}

    def test_a_non_map_is_not_facets(self):
        for value in ("a string", 42, ["a", "list"], None):
            assert _as_facets(value) == {}

    def test_an_oversized_map_is_refused_whole(self):
        """A runaway query must not be materialised into every row."""
        assert _as_facets({"blob": "x" * 3000}) == {}


class TestTheRunner:
    @staticmethod
    def _items(columns, rows):
        from src.services.feed_runner import FeedRunner
        from src.domain.named_query import NamedQuery
        q = NamedQuery(id="q1", slug="s", name="n", lang="cypher", query="")
        return list(FeedRunner._to_items(q, _Result(columns, rows)))

    def test_facets_reach_the_item(self):
        items = self._items(COLS, [_row({"buyer": "IP", "value_eur": 34769555})])
        assert items[0].facets["buyer"] == "IP"

    def test_a_query_without_the_column_yields_empty_facets(self):
        """The overwhelmingly common case: every query that predates this."""
        items = self._items(COLS[:-1], [_row({"buyer": "IP"})[:-1]])
        assert items[0].facets == {}


class TestTheApiPayload:
    def test_facets_are_serialised_for_the_card(self):
        from src.api.routers.briefings import _item_json
        item = FeedItem(item_id="i", title="t", facets={"red_flags": 2})
        assert _item_json(item)["facets"] == {"red_flags": 2}

    def test_an_item_without_facets_still_serialises(self):
        from src.api.routers.briefings import _item_json
        assert _item_json(FeedItem(item_id="i", title="t"))["facets"] == {}
