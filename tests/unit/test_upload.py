"""Tests for image upload validation logic."""
from src.infra.minio_client import ALLOWED_TYPES, MAX_SIZE, EXT_MAP


class TestUploadValidation:
    """Verify content-type and size validation constants."""

    def test_allowed_types_include_common_images(self):
        assert "image/png" in ALLOWED_TYPES
        assert "image/jpeg" in ALLOWED_TYPES
        assert "image/gif" in ALLOWED_TYPES
        assert "image/webp" in ALLOWED_TYPES

    def test_disallowed_types(self):
        assert "text/html" not in ALLOWED_TYPES
        assert "application/javascript" not in ALLOWED_TYPES
        assert "image/svg+xml" not in ALLOWED_TYPES  # SVG can contain scripts
        assert "application/pdf" not in ALLOWED_TYPES

    def test_max_size_is_5mb(self):
        assert MAX_SIZE == 5 * 1024 * 1024

    def test_ext_map_covers_all_allowed_types(self):
        for ct in ALLOWED_TYPES:
            assert ct in EXT_MAP, f"Missing extension mapping for {ct}"

    def test_ext_map_values_are_reasonable(self):
        assert EXT_MAP["image/png"] == "png"
        assert EXT_MAP["image/jpeg"] == "jpg"
        assert EXT_MAP["image/gif"] == "gif"
        assert EXT_MAP["image/webp"] == "webp"
