"""The admin user directory: one row per account, with what an admin reads it for.

Every field here is deliberately fit to show an administrator. A row is
built from an explicit projection, never from the User record, so nothing
that happens to live on an account — a password hash, a lockout counter,
the hashed IP of a session — can reach a response by being added later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, get_args

#: How the directory can be ordered. Every order is newest or largest
#: first, with accounts that have no value (never signed in, no activity)
#: last, then by id so that pages neither overlap nor skip.
DirectorySort = Literal["registered", "last_login", "last_seen", "last_activity", "activity_count"]
DIRECTORY_SORTS: tuple[str, ...] = get_args(DirectorySort)

MAX_PAGE_SIZE = 200


@dataclass
class UserDirectoryEntry:  # pylint: disable=too-many-instance-attributes
    """One account as the directory shows it."""

    id: str
    email: str
    name: str
    trust_level: str
    registered_at: datetime | None
    email_verified: bool
    #: Last successful sign-in. None until the account signs in after
    #: migration 026 — there is no earlier record to recover it from.
    last_login_at: datetime | None
    #: Last time any of the account's sessions refreshed its token, i.e.
    #: the app was open. Sessions refresh silently, so this moves without
    #: a sign-in — which is why it is not the same as last_login_at.
    last_seen_at: datetime | None
    #: Last create/update/delete the account made to a story, dossier,
    #: investigation or issue: the activity log, not page views.
    last_activity_at: datetime | None
    activity_count: int
    roles: list[str] = field(default_factory=list)


@dataclass
class UserDirectoryPage:
    """A page of the directory, with enough to page through the rest."""

    entries: list[UserDirectoryEntry]
    total: int
    limit: int
    offset: int
    sort: str
