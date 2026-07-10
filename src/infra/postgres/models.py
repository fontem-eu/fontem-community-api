from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    trust_level: Mapped[str] = mapped_column(Text, nullable=False, default="new_user")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow
    )
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    roles: Mapped[list[UserRoleModel]] = relationship(
        "UserRoleModel", back_populates="user", cascade="all, delete-orphan"
    )


class UserRoleModel(Base):
    __tablename__ = "user_roles"
    __table_args__ = (PrimaryKeyConstraint("user_id", "role"),)

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)

    user: Mapped[UserModel] = relationship("UserModel", back_populates="roles")


class GroupModel(Base):
    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Anchors the authz policy: the creator gets owner-tier rights on
    # the group (manage members, delete). Nullable for legacy rows
    # that pre-date the authz service — the policy treats null-owner
    # rows as admin-only. ondelete=SET NULL so removing a user
    # doesn't cascade-delete every group they made.
    created_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow
    )

    members: Mapped[list[GroupMemberModel]] = relationship(
        "GroupMemberModel", back_populates="group", cascade="all, delete-orphan"
    )


class GroupMemberModel(Base):
    __tablename__ = "group_members"
    __table_args__ = (PrimaryKeyConstraint("group_id", "user_id"),)

    group_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("groups.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )

    group: Mapped[GroupModel] = relationship("GroupModel", back_populates="members")


class ReportModel(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(Text, nullable=False, default="private")
    parent_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    dossier_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("dossiers.id", ondelete="SET NULL"), nullable=True
    )
    # A loose article can be linked straight to an investigation (M4), without
    # going through a dossier. SET NULL so deleting the investigation orphans it.
    investigation_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("investigations.id", ondelete="SET NULL"), nullable=True
    )
    language: Mapped[str] = mapped_column(Text, nullable=False, default="en")
    content_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    sections: Mapped[list[SectionModel]] = relationship(
        "SectionModel", back_populates="report", cascade="all, delete-orphan"
    )
    access_grants: Mapped[list[ReportAccessModel]] = relationship(
        "ReportAccessModel", back_populates="report", cascade="all, delete-orphan"
    )


class ReportAccessModel(Base):
    __tablename__ = "report_access"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    report_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=True
    )
    group_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("groups.id"), nullable=True
    )
    level: Mapped[str] = mapped_column(Text, nullable=False, default="viewer")

    report: Mapped[ReportModel] = relationship("ReportModel", back_populates="access_grants")


class ReportTranslationModel(Base):
    __tablename__ = "report_translations"
    __table_args__ = (UniqueConstraint("report_id", "lang", name="uq_report_translation_lang"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    report_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )
    lang: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class SectionModel(Base):
    __tablename__ = "sections"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    report_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    lock_holder: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=True
    )
    lock_expires: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    report: Mapped[ReportModel] = relationship("ReportModel", back_populates="sections")
    versions: Mapped[list[SectionVersionModel]] = relationship(
        "SectionVersionModel", back_populates="section", cascade="all, delete-orphan"
    )


class SectionVersionModel(Base):
    __tablename__ = "section_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    section_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("sections.id", ondelete="CASCADE"), nullable=False
    )
    content_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    saved_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    saved_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow
    )

    section: Mapped[SectionModel] = relationship("SectionModel", back_populates="versions")


class IssueModel(Base):
    __tablename__ = "issues"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    issue_type: Mapped[str] = mapped_column(Text, nullable=False, default="other")
    entity_type: Mapped[str] = mapped_column(Text, nullable=False, default="")
    entity_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    created_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class CommentModel(Base):
    __tablename__ = "comments"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    parent_type: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parent_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow
    )


class IssueVoteModel(Base):
    __tablename__ = "issue_votes"
    __table_args__ = (PrimaryKeyConstraint("issue_id", "user_id"),)

    issue_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)


class FlagModel(Base):
    __tablename__ = "flags"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    target_type: Mapped[str] = mapped_column(Text, nullable=False, default="")
    target_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="other")
    details: Mapped[str] = mapped_column(Text, nullable=False, default="")
    flagged_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow
    )


class SanctionModel(Base):
    __tablename__ = "sanctions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False, default="warning")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    starts_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    applied_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    lifted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )


class ModerationLogModel(Base):
    __tablename__ = "moderation_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    actor_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow
    )


class ConversationModel(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    report_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )
    messages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


# ── Tags: per-story association + per-user follow list ────────
# Tags are slugs (lowercase, [a-z0-9-]); the alembic migration
# enforces this at the DB level via a CHECK constraint and the
# service layer pre-normalises before insert.

class StoryTagModel(Base):
    __tablename__ = "story_tags"

    report_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("reports.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow,
    )


class UserFollowedTagModel(Base):
    __tablename__ = "user_followed_tags"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag: Mapped[str] = mapped_column(Text, primary_key=True)
    followed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow,
    )


# ── flowers_given ────────────────────────────────────────────
# Medium-style clap. One row per (user, story) holding the count of
# flowers that user has given to that story. The service layer caps
# the count at MAX_FLOWERS_PER_USER (50) per row; the CHECK constraint
# in the alembic migration mirrors that cap at the DB level.
#
# Unique by (user_id, report_id) — composite PK is the obvious choice
# for an aggregate that's read both ways (mine for the signed-in user,
# total via SUM(count) across the report).

class FlowerGivenModel(Base):
    __tablename__ = "flowers_given"
    # Prod ships via create_all (no alembic in this repo's prod path),
    # so the CHECK + report_id index live here too — not just in
    # migration 004 — otherwise the cap's DB backstop and the
    # SUM(count) hot-path index never reach prod.
    __table_args__ = (
        CheckConstraint(
            "count >= 0 AND count <= 50",
            name="flowers_given_count_check",
        ),
        Index("ix_flowers_given_report_id", "report_id"),
    )

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    report_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("reports.id", ondelete="CASCADE"),
        primary_key=True,
    )
    count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False,
        default=_utcnow, onupdate=_utcnow,
    )


# ── authz_audit ──────────────────────────────────────────
# Central record of every authorization decision the
# AuthorizationService makes. See src/services/authz/audit.py for the
# write path. ``user_id`` intentionally NOT a FK so the row survives
# user deletion (an audit trail that gets deleted with the actor is
# useless). Plain-text ``action`` (not an enum) so adding a new Action
# doesn't require an ALTER TYPE on a hot table.

class AuthzAuditModel(Base):
    __tablename__ = "authz_audit"
    __table_args__ = (
        Index("ix_authz_audit_user_timestamp", "user_id", "timestamp"),
        Index("ix_authz_audit_action_timestamp", "action", "timestamp"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid,
    )
    timestamp: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow,
    )
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True,
    )
    allowed: Mapped[bool] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

class RefreshTokenFamilyModel(Base):
    """A *family* is one continuous chain of refresh tokens that belong
    to the same login session — created once at login, rotated on every
    refresh, killed on logout or refresh-token-reuse detection.

    The novel security property the family enables: if an attacker ever
    replays a stolen refresh token, the next legitimate refresh on the
    same family finds the chain already advanced and revokes the entire
    family. Pre-launch this protects against the "stolen JWT was used
    for a month" class of bug that the 2026-06-11 review (#6) flagged.

    Cleanup: rows are kept past ``revoked_at`` for forensics; a periodic
    job (out of scope here) can DELETE families whose ``expires_at`` is
    in the past. Until that ships, prune by hand if pg disk pressure
    forces it.
    """

    __tablename__ = "refresh_token_families"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # SHA-256 hash of the current refresh token's secret. Plaintext is
    # NEVER stored — a DB dump shouldn't hand attackers live sessions.
    current_token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # When the *current* token was minted. Combined with the per-family
    # TTL window this gives us "if no refresh in 14 days, expire."
    rotated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow,
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False,
    )
    # Non-null = family killed. Either user logout, reuse detection, or
    # "sign out everywhere." Once set, no further refresh succeeds.
    revoked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Loose forensic fingerprints — SHA-256 hashes so a DB leak doesn't
    # surface raw IPs. Help answer "did this family come from the same
    # browser as that one?" without storing PII.
    created_user_agent_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_ip_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_refresh_token_families_user_id", "user_id"),
        # Hash lookup is the per-refresh hot path; without this every
        # refresh would be a sequential scan.
        Index(
            "ix_refresh_token_families_current_token_hash",
            "current_token_hash",
        ),
    )

class AuthTokenModel(Base):
    """Single-use, hashed tokens for email verification + password reset.

    One table, two purposes (``purpose`` column) so the verification
    and reset flows share the same single-use + expiry + hash
    machinery. Plaintext tokens are NEVER stored — only their SHA-256
    hash, so a DB dump can't be replayed into account takeover.

    ``consumed_at`` non-null = spent. A token is valid iff it exists,
    matches the offered hash, hasn't been consumed, and hasn't
    expired. The index on ``token_hash`` makes the consume path a
    single PK-equivalent lookup.
    """

    __tablename__ = "auth_tokens"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # 'verify_email' | 'password_reset'
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow,
    )

    __table_args__ = (
        Index("ix_auth_tokens_token_hash", "token_hash"),
        Index("ix_auth_tokens_user_id_purpose", "user_id", "purpose"),
    )



# ── Investigations ───────────────────────────────────────────
# Aggregating workspace (M1). Ships via create_all (no alembic in this
# repo's prod path — same as the other models here). Membership is
# capability flags, not a linear role; created_by is the founding owner.

class InvestigationModel(Base):
    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # SET NULL (not CASCADE) so deleting a user doesn't wipe investigations
    # they founded — mirrors GroupModel.created_by.
    created_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    members: Mapped[list[InvestigationMemberModel]] = relationship(
        "InvestigationMemberModel",
        back_populates="investigation",
        cascade="all, delete-orphan",
    )


class InvestigationMemberModel(Base):
    __tablename__ = "investigation_members"
    __table_args__ = (PrimaryKeyConstraint("investigation_id", "user_id"),)

    investigation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("investigations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False, default="viewer")

    investigation: Mapped[InvestigationModel] = relationship(
        "InvestigationModel", back_populates="members"
    )


# ── Dossiers ─────────────────────────────────────────────────
# Thin tree-of-articles structuring construct (M3). Articles point at a
# dossier via reports.dossier_id and arrange into a tree via reports.parent_id.
# Ships via create_all (this repo's prod path).

class DossierModel(Base):
    __tablename__ = "dossiers"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    investigation_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("investigations.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow
    )


class VisualizationModel(Base):
    # Server-side saved visualization (the pocket's successor). `config` is the
    # client-side widget recipe (JSONB). New table -> create_all provisions it,
    # so no manual ALTER needed (unlike columns added to existing tables).
    __tablename__ = "visualizations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    widget_type: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    investigation_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("investigations.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class ResourceGrantModel(Base):
    # Generic per-item access grant (Phase C) for dossiers + viz — the additive
    # override. New table -> create_all provisions it, no manual ALTER.
    __tablename__ = "resource_grants"
    __table_args__ = (
        Index("ix_resource_grants_lookup", "resource_type", "resource_id"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[str] = mapped_column(Text, nullable=False, default="viewer")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class ActivityLogModel(Base):
    """A user's create/update/delete events, for the activity feed."""
    __tablename__ = "activity_log"
    __table_args__ = (
        Index("ix_activity_log_actor", "actor_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    actor_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    action: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow
    )


class DataProjectModel(Base):
    # Data Studio project — container for queries + plots. Owned by its creator;
    # optionally attached to an investigation so members inherit access. The
    # investigation_id column is added to the existing table via an explicit
    # ALTER in the app lifespan (create_all only creates new tables, it never
    # adds columns to existing ones) — see app.py.
    __tablename__ = "data_projects"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    investigation_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("investigations.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    queries: Mapped[list[DataQueryModel]] = relationship(
        "DataQueryModel", back_populates="project",
        cascade="all, delete-orphan", order_by="DataQueryModel.sort_order", lazy="selectin",
    )
    plots: Mapped[list[DataPlotModel]] = relationship(
        "DataPlotModel", back_populates="project",
        cascade="all, delete-orphan", order_by="DataPlotModel.sort_order", lazy="selectin",
    )


class DataQueryModel(Base):
    __tablename__ = "data_queries"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    project_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("data_projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    lang: Mapped[str] = mapped_column(Text, nullable=False, default="cypher")
    query: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    project: Mapped[DataProjectModel] = relationship("DataProjectModel", back_populates="queries")


class DataPlotModel(Base):
    __tablename__ = "data_plots"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    project_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("data_projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    project: Mapped[DataProjectModel] = relationship("DataProjectModel", back_populates="plots")


class UserProfileModel(Base):
    # Public-profile extras (summary + labelled links). Separate table so it
    # ships via create_all (a NEW table) rather than an ALTER on the users
    # table — this repo's prod path has no alembic.
    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    links: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    avatar_x: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    avatar_y: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
