"""Rewrite ``/uploads/<key>`` references in a response payload to
freshly-minted presigned URLs.

Stories store image references as ``/uploads/<report_id>/<uuid>.<ext>``
inside TipTap JSON (v2 docs) or inline HTML (v1 docs). The bucket
itself is private — every read has to come through a signed URL the
API mints after the authz check. This module is the single seam where
that rewriting happens so the policy decision and the URL minting
stay co-located.

The walker is intentionally generic — give it any JSON-ish value
(dict, list, primitive) plus a ``mint_fn`` taking the bare key and
returning the presigned URL, and it returns a structurally identical
value with the strings rewritten. Caller-side discipline: only call
this after the authz gate has fired, otherwise an attacker can mint
URLs by triggering 404 responses that happen to echo the key in the
body.
"""
from __future__ import annotations

import re
from typing import Callable

# Matches the canonical upload reference: ``/uploads/<report_id>/<filename>``
# where the filename component holds a hex uuid + extension. Tight
# enough not to false-match arbitrary URL paths in TipTap link nodes,
# loose enough to handle SVG/.png/.jpg/.webp/.gif/.bin extensions.
# Anchored on the leading slash so a bare substring like "uploads/foo"
# inside a story body doesn't get rewritten.
_UPLOAD_RE = re.compile(
    r"/uploads/([0-9a-fA-F-]+/[0-9a-fA-F]+\.[a-z0-9]+)"
)


def presign_uploads(value, mint_fn: Callable[[str], str]):
    """Return ``value`` with every ``/uploads/<key>`` string replaced.

    Walks recursively into ``dict`` and ``list``; strings are scanned
    with ``_UPLOAD_RE`` and any match is replaced with ``mint_fn(<key>)``.
    Other primitives pass through unchanged.

    The function builds new container objects rather than mutating in
    place — the domain-layer Section/Report dataclasses are sometimes
    shared across requests after caching changes, and an in-place
    mutation would leak the per-request signed URLs back into the
    domain cache.
    """
    if isinstance(value, dict):
        return {k: presign_uploads(v, mint_fn) for k, v in value.items()}
    if isinstance(value, list):
        return [presign_uploads(v, mint_fn) for v in value]
    if isinstance(value, str):
        return _UPLOAD_RE.sub(lambda m: mint_fn(m.group(1)), value)
    return value
