"""Keyset paging for list endpoints: newest activity first, a page at a time.

A list endpoint here returns a plain JSON array, ordered by
``(updated_at DESC, id DESC)``. To get the next page, pass
``before=<updated_at>|<id>`` of the last row you received; a page shorter than
the ``limit`` you asked for is the last one. The response shape is the same
array it always was, so a client that ignores paging still works — it just
sees the first page.

Keyset rather than offset because these lists change under the reader: a
project saved while you scroll moves to the top, and with ``OFFSET`` every
row after it would shift by one and one of them would be shown twice.
"""
from __future__ import annotations

from datetime import datetime


def decode_before(raw: str) -> tuple[datetime, str] | None:
    """Parse an ``<updated_at iso>|<id>`` cursor; None when unusable.

    A garbled cursor returns the newest page rather than an error. It is an
    opaque token the client got from us; when it is wrong, the start of the
    list is a useful answer and a 422 is not one the UI can act on.
    """
    if not raw:
        return None
    stamp, _, row_id = raw.partition("|")
    if not stamp or not row_id:
        return None
    try:
        return datetime.fromisoformat(stamp), row_id
    except ValueError:
        return None
