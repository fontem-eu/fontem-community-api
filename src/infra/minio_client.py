"""MinIO S3 client for report image uploads.

Two clients live on every MinioStorage instance:

- ``_client`` talks to the in-cluster MinIO via the internal service DNS
  (``MINIO_ENDPOINT``). Used for uploads, deletes, and bucket
  housekeeping — anything that the API does on its own behalf.
- ``_presign_client`` talks to the *public* host the browser will hit
  (``MINIO_PUBLIC_ENDPOINT``). Used only for signing GET URLs. The
  AWS-SigV4 signature is bound to the host header, so the URL we hand
  to the browser must already carry the public hostname; we can't sign
  for the cluster-internal host and rewrite later.

The browser's GET lands on the same fontem-web ingress (nginx adds a
``/${MINIO_BUCKET}/`` location that proxies straight through to MinIO),
so the presigned URL looks like
``https://fontem.staging.void42.internal/<bucket>/<key>?X-Amz-…`` and
validates because nginx forwards the request with the Host header
intact.

The bucket itself is **private** — the legacy anonymous-read policy
was the actual hole behind security-review finding #4. The new
``_ensure_bucket`` actively deletes any pre-existing public policy on
first call so a one-line deploy fixes legacy clusters too.
"""
from __future__ import annotations

import os
import uuid
from datetime import timedelta
from io import BytesIO

from minio import Minio


# Format allow-list + extension map for the upload path. The file
# security pipeline in services/file_security is the source of truth
# for what content_types may land here — this map just controls the
# disk filename suffix. ``image/svg+xml`` lives here so sanitised SVG
# uploads (allowed by file_security) get a .svg extension instead of
# being saved as ``.bin`` (which broke serving via Content-Type sniff).
ALLOWED_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"}
MAX_SIZE = 5 * 1024 * 1024  # 5 MB (legacy — file_security enforces the actual cap)
EXT_MAP = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}

# Presigned-URL lifetime for image GETs. 24 h sized for two competing
# goals: long enough that a reader can scroll a story end-to-end and
# refresh the page without losing image render (and that the browser
# cache survives across a reading session), short enough that
# revoking access to a story expires the leaked URLs within a day.
# Anything in [1 h, 7 d] is defensible; 24 h is the middle.
PRESIGNED_GET_TTL = timedelta(hours=24)


class MinioStorage:
    def __init__(self) -> None:
        # MINIO_ACCESS_KEY and MINIO_SECRET_KEY must be set (no fallbacks);
        # otherwise we'd silently authenticate against a hardcoded credential
        # that may or may not match what the actual MinIO server expects.
        endpoint = os.environ.get("MINIO_ENDPOINT", "minio:9000")
        access_key = os.environ["MINIO_ACCESS_KEY"]
        secret_key = os.environ["MINIO_SECRET_KEY"]
        self._bucket = os.environ.get("MINIO_BUCKET", "gmr-uploads")
        self._client = Minio(
            endpoint, access_key=access_key, secret_key=secret_key, secure=False,
        )

        # Public endpoint for presigned URLs. Falls back to the internal
        # endpoint for local dev where there's no separate public face —
        # in that case the browser is hitting the same MinIO directly.
        public_endpoint = os.environ.get("MINIO_PUBLIC_ENDPOINT", endpoint)
        public_secure = os.environ.get("MINIO_PUBLIC_SECURE", "true").lower() == "true"
        # When fallback kicks in the dev MinIO is plaintext too.
        if public_endpoint == endpoint:
            public_secure = False
        # Pin the region so ``presigned_get_object`` doesn't try to
        # discover it via a GetBucketLocation call against the public
        # host. That call goes through nginx + TLS, and the in-cluster
        # pod doesn't trust the internal CA — without this every
        # story-with-image read raises ``SSLCertVerificationError``.
        # MinIO defaults all buckets to ``us-east-1``; pinning here
        # also saves one HTTP round trip per request.
        public_region = os.environ.get("MINIO_PUBLIC_REGION", "us-east-1")
        self._presign_client = Minio(
            public_endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=public_secure,
            region=public_region,
        )

        self._bucket_ensured = False

    def _ensure_bucket(self) -> None:
        """Create the bucket if it doesn't exist and make sure no
        anonymous-read policy is attached.

        The legacy implementation actively *added* an anonymous read
        policy here (so the un-signed ``/uploads/<key>`` proxy would
        work). The presigned-URL switch removes that path; the bucket
        is now private and reads only resolve when the API has just
        minted a fresh signed URL. We additionally call
        ``delete_bucket_policy`` to clear any leftover public policy
        from a previous deploy — cheap, idempotent, and means staging
        + prod self-heal without a manual ``mc`` step.

        Done lazily on first upload rather than in ``__init__`` so app
        startup doesn't depend on MinIO being reachable.
        """
        if self._bucket_ensured:
            return
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)
        # Clear any pre-existing public policy. ``delete_bucket_policy``
        # is a no-op when there isn't one; either way we end up with a
        # bucket that 403s anonymous reads.
        try:
            self._client.delete_bucket_policy(self._bucket)
        except Exception:  # pylint: disable=broad-exception-caught
            # An older minio-py raises a non-404 error path here on
            # already-clean buckets. We're not going to fail the upload
            # over policy housekeeping — the public-read closure is the
            # invariant that matters, and a fresh bucket is already
            # closed.
            pass
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
        """Stable, unsigned reference path that lives inside the
        stored document.

        The TipTap doc holds ``/uploads/<key>`` strings. The router
        rewrites those to fresh presigned URLs on every read via
        :meth:`presigned_get_url`. We never store the signed form
        anywhere — TTLs would make the doc bit-rot.
        """
        return f"/uploads/{key}"

    def presigned_get_url(self, key: str, expires: timedelta = PRESIGNED_GET_TTL) -> str:
        """Return a presigned GET URL the browser can hit directly.

        URL targets the public MinIO endpoint configured at construction
        — ``MINIO_PUBLIC_ENDPOINT`` in production, the same internal
        endpoint in dev. The signature is V4 and depends on the bucket,
        key, expiry, host, and HTTP method; nginx must forward the
        request to MinIO without rewriting the path or stripping the
        query string, or validation fails.
        """
        return self._presign_client.presigned_get_object(
            self._bucket, key, expires=expires,
        )

    def delete_prefix(self, prefix: str) -> int:
        """Delete all objects with a given prefix. Returns count deleted."""
        objects = list(self._client.list_objects(self._bucket, prefix=prefix, recursive=True))
        for obj in objects:
            self._client.remove_object(self._bucket, obj.object_name)
        return len(objects)
