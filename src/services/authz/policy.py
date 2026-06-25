"""Authorisation policy table — plain Python decision functions.

The policy is a dispatch from :class:`Action` to a function that
takes the caller's :class:`Principal` and (optionally) a
:class:`ResourceRef` and returns a :class:`Decision`. New actions add
a new entry. Each function is small, side-effect-free, and trivially
unit-testable.

Layers, in evaluation order:

1. **Sanctions** — an active ``ban`` deny-lists every action; an
   active ``suspend`` deny-lists state-mutating actions (read-only
   actions still go through).
2. **Roles** — explicit role assignments override trust level (e.g.
   ``admin`` can do anything; ``moderator`` can use moderation
   actions even if their trust_level is below).
3. **Trust level** — global capability ranking (new_user <
   commenter < contributor < moderator < admin).
4. **Resource ownership** — the user who created the resource gets
   all rights on it.
5. **Resource grants** — explicit access entries from a side table
   (the existing :class:`PermissionRepository` for stories; the new
   group-membership table for groups).

The functions in this module are *pure* — they do not hit the
database. Anything they need has to be inside the Principal +
ResourceRef snapshot the AuthorizationService loads up front. This
keeps each decision O(1) and makes testing dead simple.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from src.domain.investigation_roles import ROLE_TO_LEVEL, role_at_least
from src.services.authz.actions import Action


# ── Trust level ranking ──────────────────────────────────────
# Single source of truth for the ordering. Used by the trust-level
# gates below ("at least contributor") and by the moderation service
# for backward compatibility.
TRUST_RANK: dict[str, int] = {
    "new_user": 0,
    "commenter": 1,
    "contributor": 2,
    "moderator": 3,
    "admin": 4,
}


# Reason string used by every admin-override branch. Single source so
# the audit log uses a stable value; Sonar S1192 also requires it.
_ADMIN_OVERRIDE_REASON = "admin override"


def _trust_at_least(p: "Principal", min_level: str) -> bool:
    """True if the principal's trust level meets ``min_level``.

    Uses the global ``TRUST_RANK`` ordering; unknown trust strings
    rank as 0 (least privilege).
    """
    return TRUST_RANK.get(p.trust_level, 0) >= TRUST_RANK.get(min_level, 0)


# ── Snapshots ────────────────────────────────────────────────


@dataclass(frozen=True)
class Principal:
    """A snapshot of the caller at decision time.

    Built by :meth:`AuthorizationService.principal` once per request,
    then passed into every decision so the same user's state can't
    flip between two decisions in the same handler. ``sanction`` is
    None when no active sanction exists, else the sanction's ``type``
    (``mute`` / ``suspend`` / ``ban``).
    """

    user_id: str
    trust_level: str
    roles: frozenset[str] = field(default_factory=frozenset)
    sanction: str | None = None
    # False until the account confirms its email. "Required"
    # verification gates every participation action on this (see
    # _VERIFIED_REQUIRED + evaluate()). Grandfathered + OAuth accounts
    # land here True.
    email_verified: bool = True


@dataclass(frozen=True)
class ResourceRef:
    """Lightweight view of a resource — just what the policy needs.

    Built by classmethod adapters (``ResourceRef.for_group(...)`` etc.)
    so callers don't depend on the domain model schemas. Resources
    without an ownership concept (e.g. tags, the moderation queue
    itself) pass ``owner_id=None`` and the policy gates on roles /
    trust level alone.

    ``visibility`` is None for resource kinds that don't have one
    (everything except stories).

    ``effective_grant`` is the pre-resolved access level the caller
    holds on this resource (currently only meaningful for stories,
    where the existing ``PermissionService`` answers user/group
    access grants — see ``LEVEL_HIERARCHY``). The policy is kept
    pure: callers fetch the grant via ``PermissionService.check_user_grant``
    and pass it in; the policy does the comparison.
    """

    kind: str
    id: str
    owner_id: str | None = None
    visibility: str | None = None  # 'private' | 'public_open' | 'public_auth' | None
    effective_grant: str | None = None  # 'viewer' | 'commenter' | 'editor' | 'owner' | None
    # Investigation membership snapshot (for investigations:* actions):
    # whether the caller is a member, their capability flags, owner tier.
    member_role: str | None = None  # caller's investigation role (None = not a member)

    @classmethod
    def for_group(cls, group) -> "ResourceRef":
        # Imported lazily to avoid a domain-level circular import.
        return cls(
            kind="group",
            id=group.id,
            owner_id=getattr(group, "created_by", None),
        )

    @classmethod
    def for_story(cls, story, effective_grant: str | None = None) -> "ResourceRef":
        return cls(
            kind="story",
            id=story.id,
            owner_id=getattr(story, "created_by", None),
            visibility=getattr(story, "visibility", None),
            effective_grant=effective_grant,
        )

    @classmethod
    def for_issue(cls, issue) -> "ResourceRef":
        return cls(
            kind="issue",
            id=issue.id,
            owner_id=getattr(issue, "created_by", None),
        )

    @classmethod
    def for_investigation(cls, investigation, membership=None) -> "ResourceRef":
        """Snapshot an investigation + the caller's membership. ``membership``
        is the caller's InvestigationMember (or None if not a member); its
        linear ``role`` is what the policy reasons over."""
        return cls(
            kind="investigation",
            id=investigation.id,
            owner_id=getattr(investigation, "created_by", None),
            member_role=getattr(membership, "role", None) if membership is not None else None,
        )

    @classmethod
    def for_dossier(
        cls, dossier, member_role: str | None = None, effective_grant: str | None = None,
    ) -> "ResourceRef":
        return cls(
            kind="dossier",
            id=dossier.id,
            owner_id=getattr(dossier, "created_by", None),
            member_role=member_role,
            effective_grant=effective_grant,
        )

    @classmethod
    def for_visualization(
        cls, viz, member_role: str | None = None, effective_grant: str | None = None,
    ) -> "ResourceRef":
        return cls(
            kind="visualization",
            id=viz.id,
            owner_id=getattr(viz, "created_by", None),
            member_role=member_role,
            effective_grant=effective_grant,
        )


@dataclass(frozen=True)
class Decision:
    """Verdict for a single check.

    ``reason`` is required even on ``allowed=True`` — it goes into
    the audit log and feeds the eventual ``PermissionDenied.message``
    when the verdict is deny. A grep for "no decision recorded" should
    return zero matches.
    """

    allowed: bool
    reason: str

    @classmethod
    def allow(cls, reason: str) -> "Decision":
        return cls(True, reason)

    @classmethod
    def deny(cls, reason: str) -> "Decision":
        return cls(False, reason)


# ── Sanction short-circuit ───────────────────────────────────


# Actions a suspended user can still perform — read-only views, plus
# reading their own profile so they can see the suspension itself.
_SUSPEND_ALLOWED: frozenset[str] = frozenset({
    Action.USERS_READ_SELF,
    Action.USERS_READ_PUBLIC,
    Action.STORIES_READ,
    Action.GROUPS_READ,
    Action.GROUPS_READ_MEMBERS,
    Action.ISSUES_READ,
})


# Actions that "Required" email verification gates (2026-06-13
# decision). Reads + the verification/account-management surface stay
# open so an unverified user can still see content, find the "verify"
# prompt, and manage/close their account; everything that *participates*
# (creating, commenting, voting, flowering, following, flagging,
# grouping) waits for a confirmed email. Grandfathered + OAuth accounts
# are already verified so this never bites them.
_VERIFIED_REQUIRED: frozenset[str] = frozenset({
    Action.STORIES_CREATE,
    Action.STORIES_EDIT,
    Action.STORIES_EDIT_META,
    Action.STORIES_DELETE,
    Action.STORIES_SHARE,
    Action.STORIES_UPLOAD,
    Action.STORIES_SET_TAGS,
    Action.STORIES_LOCK_SECTION,
    Action.GROUPS_CREATE,
    Action.GROUPS_MANAGE_MEMBERS,
    Action.GROUPS_DELETE,
    Action.ISSUES_CREATE,
    Action.ISSUES_COMMENT,
    Action.ISSUES_VOTE,
    Action.FLAGS_CREATE,
    Action.TAGS_FOLLOW,
    Action.FLOWERS_GIVE,
    Action.INVESTIGATIONS_CREATE,
    Action.INVESTIGATIONS_EDIT_META,
    Action.INVESTIGATIONS_DELETE,
    Action.INVESTIGATIONS_MANAGE_MEMBERS,
    Action.INVESTIGATIONS_ADD_STORY,
    Action.INVESTIGATIONS_REMOVE_STORY,
    Action.INVESTIGATIONS_ADD_VIZ,
    Action.INVESTIGATIONS_REMOVE_VIZ,
    Action.DOSSIERS_CREATE,
    Action.DOSSIERS_EDIT,
    Action.DOSSIERS_DELETE,
    Action.DOSSIERS_ADD_ARTICLE,
    Action.DOSSIERS_REMOVE_ARTICLE,
    Action.VISUALIZATIONS_CREATE,
    Action.VISUALIZATIONS_EDIT,
    Action.VISUALIZATIONS_DELETE,
    Action.DOSSIERS_SHARE,
    Action.VISUALIZATIONS_SHARE,
})


def _check_email_verified(p: Principal, action: Action) -> Decision | None:
    """Deny participation actions for an unverified account, else None."""
    if action in _VERIFIED_REQUIRED and not p.email_verified:
        return Decision.deny("email not verified")
    return None


def _check_sanction(p: Principal, action: Action) -> Decision | None:
    """Return Deny if the principal's sanction blocks ``action``, else None.

    Sanctions take precedence over every other gate — a banned user
    cannot escape via the admin role.
    """
    if p.sanction == "ban":
        return Decision.deny(f"banned: {action}")
    if p.sanction == "suspend" and action not in _SUSPEND_ALLOWED:
        return Decision.deny(f"suspended: {action}")
    # 'mute' restricts commenting only — handled in the per-action
    # checks below, not here, because we want allow-by-default for
    # everything outside the commenting surface.
    return None


def _is_admin(p: Principal) -> bool:
    return "admin" in p.roles or p.trust_level == "admin"


def _is_moderator(p: Principal) -> bool:
    return _is_admin(p) or "moderator" in p.roles or p.trust_level == "moderator"


# ── Per-action policy functions ──────────────────────────────


def _self_only(p: Principal, r: ResourceRef | None) -> Decision:
    """For ``USERS_READ_SELF`` / ``USERS_DELETE_SELF`` — the resource id
    must be the caller's own user id."""
    if r is None:
        return Decision.deny("self action requires a target user")
    if r.id == p.user_id:
        return Decision.allow("self")
    if _is_admin(p):
        return Decision.allow(_ADMIN_OVERRIDE_REASON)
    return Decision.deny("not self")


def _public_read(_p: Principal, _r: ResourceRef | None) -> Decision:
    """Always-allow for public reads (the audit log still records it)."""
    return Decision.allow("public read")


def _trust_at_least_factory(min_level: str) -> Callable[[Principal, ResourceRef | None], Decision]:
    """Build a check that allows iff the caller meets ``min_level``.

    Either path satisfies the gate: an explicit role assignment for
    ``min_level`` (e.g. an ops-promoted moderator whose ``trust_level``
    column hasn't caught up yet) or a trust_level ranked at or above
    ``min_level``. This matches the legacy ModerationService check
    (``has_role or has_trust``) so the migration doesn't shift policy.
    """
    def _check(p: Principal, _r: ResourceRef | None) -> Decision:
        if _is_admin(p):
            return Decision.allow(_ADMIN_OVERRIDE_REASON)
        if min_level in p.roles:
            return Decision.allow(f"role:{min_level}")
        if _trust_at_least(p, min_level):
            return Decision.allow(f"trust_level>={min_level}")
        return Decision.deny(f"trust_level<{min_level}")
    return _check


def _owner_only(p: Principal, r: ResourceRef | None) -> Decision:
    """Allow iff the caller is the creator of the resource (or admin)."""
    if r is None:
        return Decision.deny("ownership action requires a resource")
    if _is_admin(p):
        return Decision.allow(_ADMIN_OVERRIDE_REASON)
    if r.owner_id is None:
        # Legacy row with no created_by — only admin can act.
        return Decision.deny(f"resource has no owner (legacy {r.kind})")
    if r.owner_id == p.user_id:
        return Decision.allow("owner")
    return Decision.deny(f"not owner of {r.kind} {r.id}")


def _owner_or_role(min_role: str) -> Callable[[Principal, ResourceRef | None], Decision]:
    """Allow iff admin, the resource creator, or the caller's inherited
    investigation role (member_role) is at least ``min_role``. Used by dossier
    and viz actions so investigation membership confers access to its contents.
    """
    def _check(p: Principal, r: ResourceRef | None) -> Decision:
        if r is None:
            return Decision.deny("action requires a resource")
        if _is_admin(p):
            return Decision.allow(_ADMIN_OVERRIDE_REASON)
        if r.owner_id == p.user_id:
            return Decision.allow("owner")
        if role_at_least(r.member_role, min_role):
            return Decision.allow(f"inherited:{r.member_role}>={min_role}")
        return Decision.deny(f"needs ownership or investigation role >= '{min_role}'")
    return _check


def _owner_role_or_grant(min_role: str) -> Callable[[Principal, ResourceRef | None], Decision]:
    """Allow iff admin, creator, inherited investigation role >= ``min_role``,
    or a DIRECT grant (the additive per-item override) at the equivalent level.
    """
    min_level = ROLE_TO_LEVEL[min_role]
    def _check(p: Principal, r: ResourceRef | None) -> Decision:
        if r is None:
            return Decision.deny("action requires a resource")
        if _is_admin(p):
            return Decision.allow(_ADMIN_OVERRIDE_REASON)
        if r.owner_id == p.user_id:
            return Decision.allow("owner")
        if role_at_least(r.member_role, min_role):
            return Decision.allow(f"inherited:{r.member_role}")
        if _grant_at_least(r.effective_grant, min_level):
            return Decision.allow(f"granted:{r.effective_grant}")
        return Decision.deny(f"needs ownership, role>='{min_role}', or grant>='{min_level}'")
    return _check


# Story-grant level → numeric rank. Matches PermissionService's
# LEVEL_HIERARCHY so the comparisons line up.
_GRANT_RANK: dict[str, int] = {
    "viewer": 0,
    "commenter": 1,
    "editor": 2,
    "owner": 3,
}


def _grant_at_least(grant: str | None, min_level: str) -> bool:
    """True iff ``grant`` is ranked >= ``min_level``."""
    if grant is None:
        return False
    return _GRANT_RANK.get(grant, -1) >= _GRANT_RANK.get(min_level, 0)


def _story_read(p: Principal, r: ResourceRef | None) -> Decision:
    """Read a story.

    Allows iff:
      - admin override, or
      - story is ``public_open`` (anyone, even anon — anon never
        reaches the policy though; the service builds a None
        principal and the AuthorizationService denies at the
        unauthenticated gate before evaluation), or
      - story is ``public_auth`` and principal is authenticated, or
      - principal is the story owner, or
      - principal holds an explicit grant of viewer-or-above (loaded
        from PermissionService and surfaced via
        ``ResourceRef.effective_grant``).
    """
    if r is None:
        return Decision.deny("story read requires a story")
    if _is_admin(p):
        return Decision.allow(_ADMIN_OVERRIDE_REASON)
    if r.visibility == "public_open":
        return Decision.allow("public_open")
    if r.visibility == "public_auth":
        return Decision.allow("public_auth + authenticated")
    if r.owner_id == p.user_id:
        return Decision.allow("owner")
    if _grant_at_least(r.effective_grant, "viewer"):
        return Decision.allow(f"granted {r.effective_grant}")
    return Decision.deny(f"no access to story {r.id}")


def _story_edit_factory(min_grant: str) -> Callable[[Principal, ResourceRef | None], Decision]:
    """Build an "edit-or-grant" check: allow owner or a grant of
    ``min_grant`` or above. Used for STORIES_EDIT (editor) and
    STORIES_UPLOAD / STORIES_SET_TAGS (editor — uploading or
    tagging requires write access, not just view)."""
    def _check(p: Principal, r: ResourceRef | None) -> Decision:
        if r is None:
            return Decision.deny("story edit requires a story")
        if _is_admin(p):
            return Decision.allow(_ADMIN_OVERRIDE_REASON)
        if r.owner_id == p.user_id:
            return Decision.allow("owner")
        if _grant_at_least(r.effective_grant, min_grant):
            return Decision.allow(f"granted {r.effective_grant}")
        return Decision.deny(f"no {min_grant}-or-above access to story {r.id}")
    return _check


def _issues_comment(p: Principal, _r: ResourceRef | None) -> Decision:
    """Add a comment to an issue.

    Requires ``commenter`` trust or above and no active ``mute`` /
    ``suspend`` sanction. ``mute`` lands here because it specifically
    blocks commenting.
    """
    if p.sanction in ("mute", "suspend"):
        return Decision.deny(f"sanctioned: {p.sanction}")
    if _is_admin(p):
        return Decision.allow(_ADMIN_OVERRIDE_REASON)
    if _trust_at_least(p, "commenter"):
        return Decision.allow("trust_level>=commenter")
    return Decision.deny("trust_level<commenter")


# ── Investigation checks ─────────────────────────────────────


def _inv_read(p: Principal, r: ResourceRef | None) -> Decision:
    """Investigations are member-only: any member (or the creator / admin)
    can read; everyone else is denied."""
    if r is None:
        return Decision.deny("investigation read requires a resource")
    if _is_admin(p):
        return Decision.allow(_ADMIN_OVERRIDE_REASON)
    if r.member_role == "owner" or r.owner_id == p.user_id:
        return Decision.allow("owner")
    if r.member_role is not None:
        return Decision.allow(f"member:{r.member_role}")
    return Decision.deny(f"not a member of investigation {r.id}")


def _inv_role_at_least(minimum: str) -> Callable[[Principal, ResourceRef | None], Decision]:
    """Allow iff admin, the founding creator, an owner, or the caller's
    investigation role is at least ``minimum`` (viewer<contributor<admin<owner)."""
    def _check(p: Principal, r: ResourceRef | None) -> Decision:
        if r is None:
            return Decision.deny("investigation action requires a resource")
        if _is_admin(p):
            return Decision.allow(_ADMIN_OVERRIDE_REASON)
        if r.owner_id == p.user_id:
            return Decision.allow("owner")
        if role_at_least(r.member_role, minimum):
            return Decision.allow(f"role:{r.member_role}>={minimum}")
        return Decision.deny(f"investigation role '{r.member_role}' below '{minimum}'")
    return _check


def _inv_owner(p: Principal, r: ResourceRef | None) -> Decision:
    """Owner-only investigation actions (delete)."""
    if r is None:
        return Decision.deny("investigation action requires a resource")
    if _is_admin(p):
        return Decision.allow(_ADMIN_OVERRIDE_REASON)
    if r.member_role == "owner" or r.owner_id == p.user_id:
        return Decision.allow("owner")
    return Decision.deny(f"not an owner of investigation {r.id}")
# ── Registry ─────────────────────────────────────────────────


# Action → check function. The single grep-able table that answers
# "what does it take to do X?" Add new actions at the bottom.
POLICY: dict[Action, Callable[[Principal, ResourceRef | None], Decision]] = {
    # Self
    Action.USERS_READ_SELF: _self_only,
    Action.USERS_DELETE_SELF: _self_only,
    Action.USERS_READ_PUBLIC: _public_read,

    # Stories
    Action.STORIES_CREATE: _trust_at_least_factory("new_user"),  # all signed-in
    Action.STORIES_READ: _story_read,
    Action.STORIES_EDIT: _story_edit_factory("editor"),
    Action.STORIES_EDIT_META: _story_edit_factory("owner"),  # creator or owner-grant
    Action.STORIES_DELETE: _story_edit_factory("owner"),     # creator or owner-grant
    Action.STORIES_SHARE: _owner_only,                 # grant mgmt: owner only
    Action.STORIES_UPLOAD: _story_edit_factory("editor"),
    Action.STORIES_SET_TAGS: _story_edit_factory("editor"),
    Action.STORIES_LOCK_SECTION: _story_edit_factory("editor"),

    # Groups — creation gated by trust; everything else by ownership.
    Action.GROUPS_CREATE: _trust_at_least_factory("new_user"),  # see comment
    Action.GROUPS_READ: _public_read,  # signed-in can see name/desc
    Action.GROUPS_READ_MEMBERS: _owner_only,  # only owner sees the list
    Action.GROUPS_MANAGE_MEMBERS: _owner_only,
    Action.GROUPS_DELETE: _owner_only,

    # Investigations — READ is member-only; cap-gated verbs map to the
    # membership capability flags; DELETE is owner-only.
    Action.INVESTIGATIONS_CREATE: _trust_at_least_factory("new_user"),
    Action.INVESTIGATIONS_READ: _inv_read,
    Action.INVESTIGATIONS_EDIT_META: _inv_role_at_least("admin"),
    Action.INVESTIGATIONS_MANAGE_MEMBERS: _inv_role_at_least("admin"),
    Action.INVESTIGATIONS_ADD_STORY: _inv_role_at_least("contributor"),
    Action.INVESTIGATIONS_REMOVE_STORY: _inv_role_at_least("contributor"),
    Action.INVESTIGATIONS_ADD_VIZ: _inv_role_at_least("contributor"),
    Action.INVESTIGATIONS_REMOVE_VIZ: _inv_role_at_least("contributor"),
    Action.INVESTIGATIONS_DELETE: _inv_owner,

    # Dossiers — owner-gated (creator); create gated by trust.
    Action.DOSSIERS_CREATE: _trust_at_least_factory("new_user"),
    Action.DOSSIERS_READ: _owner_role_or_grant("viewer"),
    Action.DOSSIERS_EDIT: _owner_role_or_grant("contributor"),
    Action.DOSSIERS_DELETE: _owner_role_or_grant("owner"),

    # Visualizations — owner-gated; create gated by trust.
    Action.VISUALIZATIONS_CREATE: _trust_at_least_factory("new_user"),
    Action.VISUALIZATIONS_READ: _owner_role_or_grant("viewer"),
    Action.VISUALIZATIONS_EDIT: _owner_role_or_grant("contributor"),
    Action.VISUALIZATIONS_DELETE: _owner_role_or_grant("owner"),
    Action.VISUALIZATIONS_SHARE: _owner_or_role("admin"),
    Action.DOSSIERS_ADD_ARTICLE: _owner_role_or_grant("contributor"),
    Action.DOSSIERS_REMOVE_ARTICLE: _owner_role_or_grant("contributor"),
    Action.DOSSIERS_SHARE: _owner_or_role("admin"),

    # Issues
    Action.ISSUES_CREATE: _trust_at_least_factory("new_user"),  # all signed-in (like stories)
    Action.ISSUES_READ: _public_read,
    Action.ISSUES_COMMENT: _issues_comment,
    Action.ISSUES_VOTE: _trust_at_least_factory("commenter"),
    Action.ISSUES_SET_STATUS: _trust_at_least_factory("moderator"),

    # Moderation. FLAGS_CREATE accepts any authenticated user — the
    # community needs a friction-free reporting button. Spam-tier abuse
    # is handled by the duplicate-flag conflict + the moderator queue.
    Action.FLAGS_CREATE: _trust_at_least_factory("new_user"),
    Action.FLAGS_READ_QUEUE: _trust_at_least_factory("moderator"),
    Action.FLAGS_RESOLVE: _trust_at_least_factory("moderator"),
    Action.SANCTIONS_CREATE: _trust_at_least_factory("moderator"),
    Action.SANCTIONS_BAN: _trust_at_least_factory("admin"),
    # Revocation matches the legacy behaviour (moderator-or-above) so
    # this migration PR doesn't shift policy. Tightening to admin can
    # ship as a separate change with its own audit trail.
    Action.SANCTIONS_REVOKE: _trust_at_least_factory("moderator"),
    Action.MODERATION_READ_LOG: _trust_at_least_factory("moderator"),

    # Tags
    Action.TAGS_FOLLOW: _trust_at_least_factory("new_user"),

    # Flowers — any authenticated user (the service handles cap +
    # visibility internally; this is just the "logged in" gate).
    Action.FLOWERS_GIVE: _trust_at_least_factory("new_user"),
}


def evaluate(
    principal: Principal,
    action: Action,
    resource: ResourceRef | None,
) -> Decision:
    """Single-call entry point.

    Runs the sanction short-circuit first, then dispatches to the
    per-action check. Returns a Decision either way; the caller (the
    AuthorizationService) is responsible for audit + raise.
    """
    sanction_verdict = _check_sanction(principal, action)
    if sanction_verdict is not None:
        return sanction_verdict
    verified_verdict = _check_email_verified(principal, action)
    if verified_verdict is not None:
        return verified_verdict
    check = POLICY.get(action)
    if check is None:
        # Fail closed — an action that isn't in the table is denied.
        return Decision.deny(f"no policy registered for {action}")
    return check(principal, resource)
