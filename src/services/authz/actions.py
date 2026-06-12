"""The action catalog.

Every authorisation check names an action from this enum. Adding a
new platform capability means adding a new action here and a policy
clause in :mod:`policy`. The convention is ``namespace:verb`` so an
audit-log query for "all moderation activity" is just ``WHERE action
LIKE 'moderation:%'``.

The string values are stable — they end up in the ``authz_audit``
table and in audit reports. Renaming an action is a data-migration
event; renaming the enum member is fine.
"""
from __future__ import annotations

from enum import StrEnum


class Action(StrEnum):
    # ── Self-service on the caller's own account ────────────
    USERS_READ_SELF = "users:read_self"
    USERS_DELETE_SELF = "users:delete_self"
    USERS_READ_PUBLIC = "users:read_public"  # public projection of any user

    # ── Stories / reports ─────────────────────────────────────
    STORIES_CREATE = "stories:create"
    STORIES_READ = "stories:read"
    STORIES_EDIT = "stories:edit"           # body / sections — editor grant
    # Metadata changes (title / abstract / visibility) stay owner-only:
    # flipping a story to public is a content-disclosure decision that
    # shouldn't ride on a collaborator's editor seat.
    STORIES_EDIT_META = "stories:edit_meta"
    STORIES_DELETE = "stories:delete"
    STORIES_SHARE = "stories:share"           # manage collaborator grants
    STORIES_UPLOAD = "stories:upload"          # add an attachment
    STORIES_SET_TAGS = "stories:set_tags"
    STORIES_LOCK_SECTION = "stories:lock_section"

    # ── Groups ─────────────────────────────────────────────────
    # Two layers: creating a group is a global capability gated by
    # trust level (no group exists yet so there's no owner check);
    # everything else is gated by ownership of the specific group.
    GROUPS_CREATE = "groups:create"
    GROUPS_READ = "groups:read"               # see name/description
    GROUPS_READ_MEMBERS = "groups:read_members"
    GROUPS_MANAGE_MEMBERS = "groups:manage_members"
    GROUPS_DELETE = "groups:delete"

    # ── Issues + comments ─────────────────────────────────────
    ISSUES_CREATE = "issues:create"
    ISSUES_READ = "issues:read"
    ISSUES_COMMENT = "issues:comment"
    ISSUES_VOTE = "issues:vote"
    ISSUES_SET_STATUS = "issues:set_status"

    # ── Moderation ────────────────────────────────────────────
    FLAGS_CREATE = "flags:create"
    FLAGS_READ_QUEUE = "flags:read_queue"
    FLAGS_RESOLVE = "flags:resolve"
    SANCTIONS_CREATE = "sanctions:create"
    # Bans get a separate action because banning is admin-only while
    # other sanction types (mute/suspend/warning) are moderator-level.
    # Keeping the split in the catalog means the policy table answers
    # "what does it take to ban?" in one grep.
    SANCTIONS_BAN = "sanctions:ban"
    SANCTIONS_REVOKE = "sanctions:revoke"
    MODERATION_READ_LOG = "moderation:read_log"

    # ── Tags ──────────────────────────────────────────────────
    TAGS_FOLLOW = "tags:follow"

    # ── Flowers (clap) ────────────────────────────────────────
    FLOWERS_GIVE = "flowers:give"
