"""Tests for the upload-URL rewriting helper.

Pre-fix the MinIO bucket was anonymously readable — finding #4 of
the 2026-06-11 security review. The new contract: ``/uploads/<key>``
is a stable internal reference stored in the doc; every read endpoint
mints a presigned URL on the way out via :func:`presign_uploads`.

These tests pin two properties:

1. The walker rewrites every reference, regardless of where it
   appears in the response (top-level string, nested dict, list,
   inside an HTML blob, multiple per string).
2. References that aren't real upload keys are NOT rewritten — the
   regex is anchored on the canonical shape so a story body that
   mentions ``/uploads/`` in prose doesn't get touched.
"""
from __future__ import annotations

from src.services.upload_urls import presign_uploads


def _mock_mint(key: str) -> str:
    return f"https://example.com/bucket/{key}?sig=fixed"


class TestPresignUploadsWalker:
    def test_rewrites_top_level_string(self):
        v = "/uploads/abc-def/1234.png"
        assert presign_uploads(v, _mock_mint) == (
            "https://example.com/bucket/abc-def/1234.png?sig=fixed"
        )

    def test_passes_through_non_upload_paths(self):
        assert presign_uploads("/about", _mock_mint) == "/about"
        assert presign_uploads("/uploads/", _mock_mint) == "/uploads/"
        # Looks like a key but doesn't match the canonical shape
        assert presign_uploads("/uploads/not-a-uuid", _mock_mint) == "/uploads/not-a-uuid"

    def test_rewrites_inside_html_string(self):
        html = '<p><img src="/uploads/abc-def/deadbeef.jpg"/></p>'
        out = presign_uploads(html, _mock_mint)
        assert (
            out
            == '<p><img src="https://example.com/bucket/abc-def/deadbeef.jpg?sig=fixed"/></p>'
        )

    def test_rewrites_multiple_per_string(self):
        html = '<img src="/uploads/a/1.png"/><img src="/uploads/a/2.jpg"/>'
        out = presign_uploads(html, _mock_mint)
        assert "bucket/a/1.png" in out
        assert "bucket/a/2.jpg" in out
        assert "/uploads/" not in out

    def test_walks_into_dict_and_list(self):
        # Mirror of the v2 TipTap shape — image node with src attr.
        doc = {
            "content_doc": {
                "tiptap": {
                    "type": "doc",
                    "content": [
                        {
                            "type": "image",
                            "attrs": {"src": "/uploads/0319fb3d-987c-4fc4-8d64-044a4daca389/aa11bb22ccdd.webp"},
                        },
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Plain text"}
                            ],
                        },
                    ],
                }
            },
            "sections": [],
        }
        out = presign_uploads(doc, _mock_mint)
        node = out["content_doc"]["tiptap"]["content"][0]
        assert node["attrs"]["src"].startswith("https://example.com/bucket/0319fb3d-987c-4fc4-8d64-044a4daca389/")
        # Other strings untouched.
        assert out["content_doc"]["tiptap"]["content"][1]["content"][0]["text"] == "Plain text"

    def test_preserves_non_string_primitives(self):
        v = {"id": 42, "ok": True, "tags": ["a", "b"], "score": None}
        assert presign_uploads(v, _mock_mint) == v

    def test_does_not_mutate_input(self):
        # Defensive: the domain layer occasionally hands us cached
        # Section.content_json dicts. In-place rewriting would leak
        # per-request signed URLs into the cache.
        src = "/uploads/aa-bb/123.png"
        original = {"html": src}
        out = presign_uploads(original, _mock_mint)
        assert original["html"] == src
        assert out["html"] != src
