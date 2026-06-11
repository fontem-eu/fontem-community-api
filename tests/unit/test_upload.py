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


class TestEnsureBucketPublicRead:
    """The bucket must accept anonymous GETs.

    Regression for the silent image-upload bug: upload returned 200
    with a valid URL, but the browser then hit `/uploads/<key>` and
    got a 403 because the bucket had no public-read policy. The
    images never displayed in the editor and never displayed in the
    rendered story either.
    """

    def test_anonymous_get_policy_applied_on_first_upload(self, storage_with_mock_client):  # pylint: disable=redefined-outer-name
        storage, client = storage_with_mock_client
        storage._ensure_bucket()  # pylint: disable=protected-access
        client.set_bucket_policy.assert_called_once()
        # The policy JSON must include s3:GetObject for Principal "*"
        # against the configured bucket name.
        _, policy_json = client.set_bucket_policy.call_args[0]
        policy = json.loads(policy_json)
        stmt = policy["Statement"][0]
        assert stmt["Effect"] == "Allow"
        assert stmt["Principal"]["AWS"] == "*"
        assert "s3:GetObject" in stmt["Action"]
        assert "arn:aws:s3:::test-bucket/*" in stmt["Resource"]

    def test_policy_is_set_once_per_process(self, storage_with_mock_client):  # pylint: disable=redefined-outer-name
        # _bucket_ensured short-circuit means we don't pay the
        # set_bucket_policy round-trip on every upload — only the first.
        storage, client = storage_with_mock_client
        storage._ensure_bucket()  # pylint: disable=protected-access
        storage._ensure_bucket()  # pylint: disable=protected-access
        storage._ensure_bucket()  # pylint: disable=protected-access
        assert client.set_bucket_policy.call_count == 1

    def test_new_bucket_gets_created_then_policied(self, storage_with_mock_client):  # pylint: disable=redefined-outer-name
        storage, client = storage_with_mock_client
        client.bucket_exists.return_value = False
        storage._ensure_bucket()  # pylint: disable=protected-access
        client.make_bucket.assert_called_once_with("test-bucket")
        client.set_bucket_policy.assert_called_once()

    def test_existing_bucket_gets_policy_refreshed(self, storage_with_mock_client):  # pylint: disable=redefined-outer-name
        """Even when the bucket already exists (e.g. created before the
        public-read fix shipped), the next upload after restart re-
        applies the policy. Otherwise pre-existing buckets would never
        get the fix without manual intervention.
        """
        storage, client = storage_with_mock_client
        client.bucket_exists.return_value = True
        storage._ensure_bucket()  # pylint: disable=protected-access
        client.make_bucket.assert_not_called()
        client.set_bucket_policy.assert_called_once()
