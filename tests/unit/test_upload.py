"""Tests for image upload validation logic."""
import json
from unittest.mock import MagicMock, patch

import pytest

from src.infra.minio_client import (
    ALLOWED_TYPES, EXT_MAP, MAX_SIZE, MinioStorage,
)


class TestUploadValidation:
    """Verify content-type and size validation constants."""

    def test_allowed_types_include_common_images(self):
        assert "image/png" in ALLOWED_TYPES
        assert "image/jpeg" in ALLOWED_TYPES
        assert "image/gif" in ALLOWED_TYPES
        assert "image/webp" in ALLOWED_TYPES

    def test_disallowed_types(self):
        # SVG was added to ALLOWED_TYPES once the file_security
        # pipeline learned how to sanitise it (strip <script>,
        # <foreignObject>, on* handlers, javascript: hrefs) — see
        # services/file_security.py. Test inverted accordingly.
        assert "text/html" not in ALLOWED_TYPES
        assert "application/javascript" not in ALLOWED_TYPES
        assert "image/svg+xml" in ALLOWED_TYPES  # sanitised, not banned
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


@pytest.fixture
def storage_with_mock_client(monkeypatch):
    """Spin up a MinioStorage instance with a mocked underlying client.

    Bypasses the real `minio.Minio(...)` constructor + the env-var
    assertion so each test can assert on `set_bucket_policy` /
    `put_object` calls without touching the network.
    """
    monkeypatch.setenv("MINIO_ACCESS_KEY", "test-key")
    monkeypatch.setenv("MINIO_SECRET_KEY", "test-secret")
    monkeypatch.setenv("MINIO_BUCKET", "test-bucket")
    with patch("src.infra.minio_client.Minio") as MinioCls:
        client = MagicMock()
        MinioCls.return_value = client
        storage = MinioStorage()
        # Pre-existing bucket scenario for most tests; flip on demand.
        client.bucket_exists.return_value = True
        yield storage, client


class TestEnsureBucketIsPrivate:
    """The bucket must NOT accept anonymous reads.

    Pre-2026-06-13 it did — finding #4 of the platform security
    review. The new contract: ``_ensure_bucket`` actively clears any
    pre-existing public policy on first upload (so a deploy heals
    legacy clusters without a manual ``mc`` step) and never sets
    one. Reads happen via presigned URLs minted at story-read time,
    not via bucket-level anonymous access.
    """

    def test_public_read_policy_is_never_set(self, storage_with_mock_client):  # pylint: disable=redefined-outer-name
        storage, client = storage_with_mock_client
        storage._ensure_bucket()  # pylint: disable=protected-access
        client.set_bucket_policy.assert_not_called()

    def test_pre_existing_public_policy_is_cleared(self, storage_with_mock_client):  # pylint: disable=redefined-outer-name
        # Legacy clusters have the public-read policy already attached.
        # First call to ``_ensure_bucket`` after deploy must delete it
        # so the bucket self-heals without operator intervention.
        storage, client = storage_with_mock_client
        storage._ensure_bucket()  # pylint: disable=protected-access
        client.delete_bucket_policy.assert_called_once_with("test-bucket")

    def test_ensure_runs_once_per_process(self, storage_with_mock_client):  # pylint: disable=redefined-outer-name
        # ``_bucket_ensured`` short-circuits subsequent calls so we
        # don't keep calling delete_bucket_policy on every upload.
        storage, client = storage_with_mock_client
        storage._ensure_bucket()  # pylint: disable=protected-access
        storage._ensure_bucket()  # pylint: disable=protected-access
        storage._ensure_bucket()  # pylint: disable=protected-access
        assert client.delete_bucket_policy.call_count == 1

    def test_new_bucket_gets_created_and_left_private(self, storage_with_mock_client):  # pylint: disable=redefined-outer-name
        storage, client = storage_with_mock_client
        client.bucket_exists.return_value = False
        storage._ensure_bucket()  # pylint: disable=protected-access
        client.make_bucket.assert_called_once_with("test-bucket")
        client.set_bucket_policy.assert_not_called()
