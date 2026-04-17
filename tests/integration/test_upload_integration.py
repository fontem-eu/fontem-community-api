"""Integration test for image uploads against a real MinIO container.

This test exists because production uploads silently broke when the
MINIO_* env vars weren't wired into the deployment — the code fell
back to hardcoded credentials. This test exercises the full upload
code path (router → MinioStorage → S3 client → bucket) against a
real MinIO testcontainer so the regression cannot recur.
"""
from __future__ import annotations

import io
import struct
import zlib

from tests.integration.conftest import make_headers


def _png_bytes() -> bytes:
    """Return a minimal valid 1x1 PNG (transparent pixel)."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    ihdr = b"IHDR" + ihdr_data
    ihdr_chunk = struct.pack(">I", len(ihdr_data)) + ihdr + struct.pack(
        ">I", zlib.crc32(ihdr),
    )
    raw = b"\x00\x00\x00\x00\x00"
    idat_data = zlib.compress(raw)
    idat = b"IDAT" + idat_data
    idat_chunk = struct.pack(">I", len(idat_data)) + idat + struct.pack(
        ">I", zlib.crc32(idat),
    )
    iend = b"IEND"
    iend_chunk = struct.pack(">I", 0) + iend + struct.pack(">I", zlib.crc32(iend))
    return sig + ihdr_chunk + idat_chunk + iend_chunk


class TestImageUpload:
    """UPLOAD-I01..I04: end-to-end image uploads."""

    def test_png_uploads_successfully(self, client, user_id, _minio):
        """A valid PNG upload returns 200 and stores the bytes in MinIO."""
        h = make_headers(user_id)
        report = client.post("/reports", json={"title": "WithImage"}, headers=h).json()

        png = _png_bytes()
        resp = client.post(
            f"/reports/{report['id']}/upload",
            files={"file": ("pixel.png", io.BytesIO(png), "image/png")},
            headers=h,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "key" in body and body["key"].startswith(f"{report['id']}/")
        assert body["url"].startswith("/uploads/")

        # Verify the bytes actually landed in the bucket
        s3 = _minio.get_client()
        obj = s3.get_object("gmr-uploads", body["key"])
        try:
            stored = obj.read()
        finally:
            obj.close()
            obj.release_conn()
        assert stored == png

    def test_disallowed_content_type_rejected(self, client, user_id):
        """Non-image content types are rejected with 400."""
        h = make_headers(user_id)
        report = client.post("/reports", json={"title": "BadType"}, headers=h).json()

        resp = client.post(
            f"/reports/{report['id']}/upload",
            files={"file": ("evil.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
            headers=h,
        )
        assert resp.status_code == 400
        assert "not allowed" in resp.json()["detail"].lower()

    def test_oversized_file_rejected(self, client, user_id):
        """Files over 5MB are rejected with 400."""
        h = make_headers(user_id)
        report = client.post("/reports", json={"title": "TooBig"}, headers=h).json()

        too_big = b"\x00" * (6 * 1024 * 1024)
        resp = client.post(
            f"/reports/{report['id']}/upload",
            files={"file": ("huge.png", io.BytesIO(too_big), "image/png")},
            headers=h,
        )
        assert resp.status_code == 400
        assert "too large" in resp.json()["detail"].lower()

    def test_upload_requires_editor_permission(self, client, user_id, user2_id):
        """A non-owner can't upload to someone else's report."""
        h_owner = make_headers(user_id)
        h_other = make_headers(user2_id)
        # Auto-create the second user
        client.get("/users/me", headers=h_other)
        report = client.post(
            "/reports", json={"title": "Mine"}, headers=h_owner,
        ).json()

        resp = client.post(
            f"/reports/{report['id']}/upload",
            files={"file": ("p.png", io.BytesIO(_png_bytes()), "image/png")},
            headers=h_other,
        )
        assert resp.status_code in (401, 403)
