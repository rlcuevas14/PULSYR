import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.enums import PROJECT_MEMBER_ROLES, check_in


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("account_id", "slug", name="projects_account_slug_uniq"),
        UniqueConstraint(
            "account_id", "sentry_project_slug", name="projects_account_sentry_slug_uniq"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    repo_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Per-project integration secrets (override global env vars when set)
    github_webhook_secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Sentry routing: slug de este proyecto en la org Sentry de la cuenta (spec 2026-07-10).
    # La conexión (secretos) vive a nivel cuenta en sentry_connections.
    sentry_project_slug: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProjectMember(Base):
    """Per-project grant for a collaborator (the owner has implicit access to all)."""

    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="project_members_uniq"),
        CheckConstraint(check_in("role", PROJECT_MEMBER_ROLES), name="project_members_role_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="editor")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())


class ProjectModule(Base):
    """Configured availability of one optional capability for a project."""

    __tablename__ = "project_modules"
    __table_args__ = (
        UniqueConstraint("project_id", "module", name="project_modules_project_module_uniq"),
        CheckConstraint(
            "module IN ('threads','incidents','management')",
            name="project_modules_module_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    module: Mapped[str] = mapped_column(String(30), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProjectModuleEvent(Base):
    """Append-only audit receipt for an effective module configuration change."""

    __tablename__ = "project_module_events"
    __table_args__ = (
        CheckConstraint(
            "module IN ('threads','incidents','management')",
            name="project_module_events_module_check",
        ),
        CheckConstraint(
            "source IN ('onboarding','preset','manual','migration')",
            name="project_module_events_source_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    module: Mapped[str] = mapped_column(String(30), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    previous_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
