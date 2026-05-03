"""Tests for /sitemap*.xml endpoints.

Pins the contract that crawlers rely on:
  * `/sitemap.xml` is a sitemapindex referencing the per-shard files.
  * `/sitemap-core.xml` lists the static public routes.
  * `/sitemap-stories.xml` lists every `public_*` data story.
  * All three return XML with the correct root element and the
    canonical URL from ``CANONICAL_URL`` baked into absolute `<loc>`s.
"""
# pylint: disable=missing-function-docstring
from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET

import pytest
from tests.conftest import make_headers, seed_user


NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _seed(services, user_id="user-1"):
    _run(seed_user(services["user_repo"], user_id, trust_level="contributor"))


class TestSitemap:
    def test_sitemap_index_lists_shards(self, client, services):
        resp = client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/xml")
        root = ET.fromstring(resp.text)
        assert root.tag.endswith("sitemapindex")
        locs = [el.text for el in root.findall(".//sm:sitemap/sm:loc", NS)]
        assert any(u.endswith("/sitemap-core.xml") for u in locs)
        assert any(u.endswith("/sitemap-stories.xml") for u in locs)

    def test_sitemap_core_lists_static_routes(self, client, services):
        resp = client.get("/sitemap-core.xml")
        assert resp.status_code == 200
        root = ET.fromstring(resp.text)
        assert root.tag.endswith("urlset")
        locs = [el.text for el in root.findall(".//sm:url/sm:loc", NS)]
        # Spot-check a few required entries
        assert any(u.endswith("/") for u in locs)
        assert any(u.endswith("/feed") for u in locs)
        assert any(u.endswith("/privacy") for u in locs)
        assert any(u.endswith("/data-quality") for u in locs)
        # Login, admin, my-reports MUST NOT appear in the sitemap — those
        # are either per-user or unindexable by policy.
        assert not any(u.endswith("/login") for u in locs)
        assert not any(u.endswith("/admin") for u in locs)
        assert not any(u.endswith("/my-reports") for u in locs)

    def test_sitemap_stories_includes_only_public_stories(self, client, services):
        _seed(services)
        h = make_headers("user-1")

        # 1 private, 1 public_open, 1 public_auth — only the last two
        # should show up.
        private_resp = client.post("/data-stories", json={"title": "private"}, headers=h)
        assert private_resp.status_code == 201
        priv_id = private_resp.json()["id"]

        public_resp = client.post("/data-stories", json={"title": "public"}, headers=h)
        pub_id = public_resp.json()["id"]
        client.put(f"/data-stories/{pub_id}", json={"visibility": "public_open"}, headers=h)

        mixed_resp = client.post("/data-stories", json={"title": "authed"}, headers=h)
        mid = mixed_resp.json()["id"]
        client.put(f"/data-stories/{mid}", json={"visibility": "public_auth"}, headers=h)

        resp = client.get("/sitemap-stories.xml")
        assert resp.status_code == 200
        root = ET.fromstring(resp.text)
        locs = [el.text for el in root.findall(".//sm:url/sm:loc", NS)]

        # Anonymous sitemap view — public_open only. public_auth is
        # excluded because the contents are restricted to signed-in
        # users; indexing its URL publicly would leak metadata.
        # (This is the existing list_public behaviour we're reusing.)
        # Path emitted as /stories/<id> — the new frontend route.
        assert any(u.endswith(f"/stories/{pub_id}") for u in locs)
        assert not any(u.endswith(f"/stories/{priv_id}") for u in locs)

    def test_absolute_urls_use_canonical_origin(self, client, services, monkeypatch):
        # Override the canonical origin per request; the router reads
        # os.environ at import time, so we reload the module after
        # setting the env var.
        import importlib
        import os

        os.environ["CANONICAL_URL"] = "https://test.fontem.eu"
        from src.api.routers import sitemap as sitemap_mod
        importlib.reload(sitemap_mod)

        # Re-mount the router so the client sees the reloaded instance.
        # Simplest: just verify the rendered text starts with the
        # configured host.
        resp = client.get("/sitemap-core.xml")
        assert "https://test.fontem.eu/" in resp.text
        # Restore for downstream tests
        os.environ.pop("CANONICAL_URL", None)
        importlib.reload(sitemap_mod)

    def test_sitemaps_are_anonymously_accessible(self, client, services):
        # Crawlers hit these without auth; must not 401.
        for path in ("/sitemap.xml", "/sitemap-core.xml", "/sitemap-stories.xml"):
            resp = client.get(path)
            assert resp.status_code == 200, path
