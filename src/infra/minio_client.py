"""MinIO S3 client for report image uploads."""
from __future__ import annotations

import json
import os
import uuid
from io import BytesIO

from minio import Minio


ALLOWED_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_SIZE = 5 * 1024 * 1024  # 5 MB
EXT_MAP = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}

# Anonymous-GET policy for the uploads bucket. nginx fronts the bucket
# at `/uploads/<key>` and proxies un-signed GETs straight through, so
# the bucket has to accept anonymous reads — otherwise the upload
# returns a 200 with a URL the browser then hits as a 403, which is
# exactly the "I added an image and it doesn't show up" report. Writes
# stay credentialed (POST/PUT only via the upload route).
_PUBLIC_READ_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"AWS": "*"},
            "Action": ["s3:GetObject"],
            "Resource": ["arn:aws:s3:::{bucket}/*"],
        },
    ],
}


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
        self._bucket_ensured = False

    def _ensure_bucket(self) -> None:
        """Create the bucket if it doesn't exist + grant anonymous read.

        Both steps are idempotent. Anonymous read is required because
        the editor renders <img src="/uploads/<key>">, nginx proxies
        that path to MinIO unsigned, and a private bucket would 403
        every uploaded image even though the upload itself succeeded.
        Done on first upload rather than in __init__ so app startup
        doesn't depend on MinIO being reachable.
        """
        if self._bucket_ensured:
            return
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)
        # set_bucket_policy is a PUT and replaces whatever's there. We
        # render the policy fresh from _PUBLIC_READ_POLICY so the bucket
        # name interpolates correctly even after a rename.
        policy = json.loads(
            json.dumps(_PUBLIC_READ_POLICY).replace("{bucket}", self._bucket),
        )
        self._client.set_bucket_policy(self._bucket, json.dumps(policy))
        self._bucket_ensured = True

    def upload(self, report_id: str, data: bytes, content_type: str) -> str:
        """Upload a file and return its object key."""
        self._ensure_bucket()
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
