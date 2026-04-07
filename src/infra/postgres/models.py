from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    String,
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
    trust_level: Mapped[str] = mapped_column(Text, nullable=False, default="new_user")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow
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
