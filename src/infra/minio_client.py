"""MinIO S3 client for report image uploads."""
from __future__ import annotations

import os
import uuid
from io import BytesIO

from minio import Minio


ALLOWED_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_SIZE = 5 * 1024 * 1024  # 5 MB
EXT_MAP = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}


class MinioStorage:
    def __init__(self) -> None:
        # MINIO_ACCESS_KEY and MINIO_SECRET_KEY must be set (no fallbacks);
        # otherwise we'd silently authenticate against a hardcoded credential
        # that may or may not match what the actual MinIO server expects.
        endpoint = os.environ.get("MINIO_ENDPOINT", "minio:9000")
        access_key = os.environ["MINIO_ACCESS_KEY"]
        secret_key = os.environ["MINIO_SECRET_KEY"]
        self._bucket = os.environ.get("MINIO_BUCKET", "gmr-uploads")
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False)

    def upload(self, report_id: str, data: bytes, content_type: str) -> str:
        """Upload a file and return its object key."""
        ext = EXT_MAP.get(content_type, "bin")
        key = f"{report_id}/{uuid.uuid4().hex}.{ext}"
        self._client.put_object(
            self._bucket, key, BytesIO(data), len(data), content_type=content_type,
        )
        return key

    def get_url(self, key: str) -> str:
        """Return a URL for the object (internal service URL)."""
        return f"/uploads/{key}"

    def delete_prefix(self, prefix: str) -> int:
        """Delete all objects with a given prefix. Returns count deleted."""
        objects = list(self._client.list_objects(self._bucket, prefix=prefix, recursive=True))
        for obj in objects:
            self._client.remove_object(self._bucket, obj.object_name)
        return len(objects)
