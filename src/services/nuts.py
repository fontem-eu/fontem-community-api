"""Shared NUTS region-code normalisation.

A NUTS code is a 2-letter country code plus up to three alphanumerics
(e.g. ``PT`` / ``PT1`` / ``PT17`` / ``PT170``). Used for the profile home
region and the data-story region tag.
"""
from __future__ import annotations

import re

_NUTS_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{0,3}$")


def normalize_nuts(value: str | None, current: str = "") -> str:
    """Normalise a NUTS code against the currently-stored value.

    - ``None`` -> keep ``current`` (partial update leaves it untouched).
    - ``""``   -> clear it.
    - a valid NUTS code -> stored upper-cased.
    - anything malformed -> keep ``current`` (a bad client value can't wipe a
      good one).
    """
    if value is None:
        return current
    code = value.strip().upper()
    if code == "":
        return ""
    return code if _NUTS_RE.match(code) else current
