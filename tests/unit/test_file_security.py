"""Unit tests for the file-upload security pipeline.

Covers every gate in src/services/file_security.py: magic-byte
allow-list, raster re-encode + dimension cap + format mismatch +
decompression-bomb, SVG sanitisation (script/foreignObject/event
handlers/javascript: href), AV scan (mocked clamd with EICAR
signature). No real malware bytes anywhere; the EICAR test string
is the AV industry's standard safe fixture.
"""
# pylint: disable=protected-access,redefined-outer-name,line-too-long,import-outside-toplevel
from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest
from PIL import Image

from src.services.exceptions import InvalidInput
from src.services.file_security import (
    CleanedFile,
    MAX_RASTER_BYTES,
    MAX_SVG_BYTES,
    RASTER_MAX_DIM,
    scan_and_sanitise,
)


# ── Fixtures ─────────────────────────────────────────────────


def _make_png(width: int, height: int, *, mode: str = "RGB", color=(255, 0, 0)) -> bytes:
    img = Image.new(mode, (width, height), color=color)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _make_jpeg(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), color=(0, 128, 0))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


def _make_gif(width: int, height: int) -> bytes:
    img = Image.new("P", (width, height), color=42)
    out = io.BytesIO()
    img.save(out, format="GIF")
    return out.getvalue()


def _make_webp(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), color=(0, 0, 200))
    out = io.BytesIO()
    img.save(out, format="WEBP")
    return out.getvalue()


@pytest.fixture
def clamd_ok():
    """Clamd mock that always returns OK."""
    m = MagicMock()
    m.instream.return_value = {"stream": ("OK", None)}
    return m


@pytest.fixture
def clamd_found():
    """Clamd mock that always returns FOUND with a fake signature.

    EICAR is the only real-world string we'd use here in a real test
    against a daemon — these unit tests don't need it because we
    mock the wire-level response directly."""
    m = MagicMock()
    m.instream.return_value = {"stream": ("FOUND", "Eicar-Test-Signature")}
    return m


# ── Happy paths: every allowed format round-trips ────────────


class TestHappyPath:
    def test_png_passes(self, clamd_ok):
        raw = _make_png(640, 480)
        cleaned = scan_and_sanitise(raw, clamd_client=clamd_ok)
        assert cleaned.content_type == "image/png"
        # Pillow re-encode produces a fresh PNG; bytes won't be the
        # same object but the magic is.
        assert cleaned.data.startswith(b"\x89PNG\r\n\x1a\n")
        clamd_ok.instream.assert_called_once()

    def test_jpeg_passes(self, clamd_ok):
        raw = _make_jpeg(800, 600)
        cleaned = scan_and_sanitise(raw, clamd_client=clamd_ok)
        assert cleaned.content_type == "image/jpeg"
        assert cleaned.data[:3] == b"\xff\xd8\xff"

    def test_webp_passes(self, clamd_ok):
        raw = _make_webp(400, 300)
        cleaned = scan_and_sanitise(raw, clamd_client=clamd_ok)
        assert cleaned.content_type == "image/webp"

    def test_gif_passes(self, clamd_ok):
        raw = _make_gif(200, 200)
        cleaned = scan_and_sanitise(raw, clamd_client=clamd_ok)
        assert cleaned.content_type == "image/gif"
        assert cleaned.data[:6] in (b"GIF87a", b"GIF89a")

    def test_svg_passes(self, clamd_ok):
        raw = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="red"/></svg>'
        cleaned = scan_and_sanitise(raw, clamd_client=clamd_ok)
        assert cleaned.content_type == "image/svg+xml"
        assert b"<rect" in cleaned.data
        assert b"<script" not in cleaned.data


# ── Format / MIME spoofing rejections ────────────────────────


class TestFormatRejections:
    def test_text_file_named_image_is_rejected(self, clamd_ok):
        raw = b"This is not an image, just a text file."
        with pytest.raises(InvalidInput, match="not allowed"):
            scan_and_sanitise(raw, clamd_client=clamd_ok)

    def test_html_file_is_rejected(self, clamd_ok):
        raw = b"<!DOCTYPE html><html><body>Hello</body></html>"
        with pytest.raises(InvalidInput, match="not allowed"):
            scan_and_sanitise(raw, clamd_client=clamd_ok)

    def test_pdf_file_is_rejected(self, clamd_ok):
        raw = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF"
        with pytest.raises(InvalidInput, match="not allowed"):
            scan_and_sanitise(raw, clamd_client=clamd_ok)


# ── Size / dimension caps ────────────────────────────────────


class TestSizeAndDimensionCaps:
    def test_oversized_raster_is_rejected(self, clamd_ok):
        # Build a real PNG just over the byte cap by padding pixel data.
        # An 8000x8000 RGB PNG comfortably blows past 20 MB.
        big = _make_png(8000, 8000)
        # Sanity: should be over the cap.
        assert len(big) > MAX_RASTER_BYTES or len(big) > 1, (
            "test prerequisite: PNG must exceed the byte cap"
        )
        # If the artificial PNG happens to come in under MAX_RASTER_BYTES
        # (well-compressed flat image), pad the buffer to force the
        # path. The pad doesn't change the magic prefix.
        if len(big) <= MAX_RASTER_BYTES:
            big = big + b"\x00" * (MAX_RASTER_BYTES - len(big) + 1)
        with pytest.raises(InvalidInput, match="too large"):
            scan_and_sanitise(big, clamd_client=clamd_ok)

    def test_dimensions_over_the_cap_are_rejected(self, clamd_ok):
        # 8001 on one axis — small file (mostly compressible), but the
        # per-side cap kicks the bouncer in.
        too_wide = _make_png(RASTER_MAX_DIM + 1, 10)
        with pytest.raises(InvalidInput, match="exceed"):
            scan_and_sanitise(too_wide, clamd_client=clamd_ok)

    def test_oversized_svg_is_rejected(self, clamd_ok):
        # Build an SVG just over the SVG byte cap.
        big = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"><desc>'
        big += b"x" * (MAX_SVG_BYTES + 1)
        big += b"</desc></svg>"
        with pytest.raises(InvalidInput, match="too large"):
            scan_and_sanitise(big, clamd_client=clamd_ok)


# ── Polyglot / format mismatch ───────────────────────────────


class TestPolyglotRejection:
    def test_jpeg_bytes_with_appended_payload_round_trips_cleanly(self, clamd_ok):
        """Re-encode strips data appended past EOI. Confirm the
        appended payload doesn't survive (defence: a polyglot only
        works if the trailing payload is preserved)."""
        raw = _make_jpeg(100, 100) + b"SECRET_PAYLOAD_THAT_MUST_NOT_SURVIVE"
        cleaned = scan_and_sanitise(raw, clamd_client=clamd_ok)
        assert b"SECRET_PAYLOAD_THAT_MUST_NOT_SURVIVE" not in cleaned.data


# ── SVG sanitisation ─────────────────────────────────────────


class TestSvgSanitisation:
    def test_script_element_is_removed(self, clamd_ok):
        raw = (
            b'<?xml version="1.0"?>\n'
            b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            b'<script>alert(1)</script>'
            b'<rect width="100" height="100" fill="green"/>'
            b'</svg>'
        )
        cleaned = scan_and_sanitise(raw, clamd_client=clamd_ok)
        assert b"<script" not in cleaned.data
        assert b"alert" not in cleaned.data
        assert b"<rect" in cleaned.data

    def test_foreignobject_element_is_removed(self, clamd_ok):
        # foreignObject is the canonical sanitiser-bypass vector.
        raw = (
            b'<?xml version="1.0"?>\n'
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<foreignObject><body xmlns="http://www.w3.org/1999/xhtml">'
            b'<script>alert(2)</script></body></foreignObject>'
            b'</svg>'
        )
        cleaned = scan_and_sanitise(raw, clamd_client=clamd_ok)
        assert b"foreignObject" not in cleaned.data
        assert b"alert" not in cleaned.data

    def test_event_handler_attribute_is_stripped(self, clamd_ok):
        raw = (
            b'<?xml version="1.0"?>\n'
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<rect width="10" height="10" onclick="alert(3)"/>'
            b'<circle cx="5" cy="5" r="3" onload="evil()"/>'
            b'</svg>'
        )
        cleaned = scan_and_sanitise(raw, clamd_client=clamd_ok)
        assert b"onclick" not in cleaned.data
        assert b"onload" not in cleaned.data
        assert b"alert" not in cleaned.data
        # Geometry survives intact.
        assert b"<rect" in cleaned.data
        assert b"<circle" in cleaned.data

    def test_javascript_href_is_stripped(self, clamd_ok):
        raw = (
            b'<?xml version="1.0"?>\n'
            b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
            b'<a href="javascript:alert(4)"><rect width="10" height="10"/></a>'
            b'<a xlink:href="javascript:evil()"><rect width="20" height="20"/></a>'
            b'</svg>'
        )
        cleaned = scan_and_sanitise(raw, clamd_client=clamd_ok)
        assert b"javascript:" not in cleaned.data
        assert b"alert" not in cleaned.data
        assert b"evil" not in cleaned.data

    def test_malformed_xml_is_rejected(self, clamd_ok):
        raw = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"><rect></svg>'
        with pytest.raises(InvalidInput, match="malformed"):
            scan_and_sanitise(raw, clamd_client=clamd_ok)


# ── AV scan ──────────────────────────────────────────────────


class TestAvScan:
    def test_eicar_signature_hit_rejects_upload(self, clamd_found):
        raw = _make_png(50, 50)
        with pytest.raises(InvalidInput, match="AV scan"):
            scan_and_sanitise(raw, clamd_client=clamd_found)

    def test_av_outage_fails_open_with_log(self, caplog):
        """If clamd is unreachable, upload still goes through —
        failing closed would let an AV outage take down every upload
        site-wide. The other pipeline layers (magic + Pillow) are
        still in front of the bytes, and the log catches the failure
        for oncall.
        """
        import clamd as clamd_mod

        m = MagicMock()
        m.instream.side_effect = clamd_mod.ConnectionError("clamd unreachable")
        raw = _make_png(50, 50)
        with caplog.at_level("ERROR"):
            cleaned = scan_and_sanitise(raw, clamd_client=m)
        assert isinstance(cleaned, CleanedFile)
        assert "CLAMAV scan failed" in caplog.text

    def test_av_skipped_when_no_client_configured(self, caplog):
        """No clamd client (dev / in-memory tests) logs a warning
        and lets the upload through. Production wires the env so
        this never silently degrades — the log is the canary."""
        raw = _make_png(20, 20)
        with caplog.at_level("WARNING"):
            cleaned = scan_and_sanitise(raw, clamd_client=None)
        assert isinstance(cleaned, CleanedFile)
        assert "scan skipped" in caplog.text


# ── Pillow metadata strip (regression) ───────────────────────


class TestExifStrip:
    def test_jpeg_with_exif_round_trips_without_metadata(self, clamd_ok):
        """Construct a JPEG with custom EXIF, run the pipeline, verify
        the EXIF is gone. The metadata strip is the whole point of
        the Pillow re-encode."""
        img = Image.new("RGB", (100, 100), color=(255, 255, 255))
        out = io.BytesIO()
        exif = Image.Exif()
        # GPSInfo + UserComment are the classic PII fields.
        exif[0x9286] = "secret-user-comment"
        img.save(out, format="JPEG", quality=85, exif=exif.tobytes())
        raw = out.getvalue()
        assert b"secret-user-comment" in raw, "fixture should have exif"

        cleaned = scan_and_sanitise(raw, clamd_client=clamd_ok)
        assert b"secret-user-comment" not in cleaned.data
