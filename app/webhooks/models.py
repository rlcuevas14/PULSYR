"""ORM de sentry_issues (la tabla existe desde v0001; aquí el modelo para ORM/tests)."""

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, TIMESTAMP, CheckConstraint, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.enums import SENTRY_LEVELS, SENTRY_STATUSES, SENTRY_TRIAGE, check_in


class SentryIssue(Base):
    __tablename__ = "sentry_issues"
    __table_args__ = (
        CheckConstraint(check_in("level", SENTRY_LEVELS), name="sentry_issues_level_check"),
        CheckConstraint(
            f"triage IS NULL OR {check_in('triage', SENTRY_TRIAGE)}",
            name="sentry_issues_triage_check",
        ),
        CheckConstraint(
            check_in("status", SENTRY_STATUSES), name="sentry_issues_status_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    # Cuenta dueña del evento (v0017): hace tenancy-safe a las filas sin proyecto.
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True
    )
    sentry_issue_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    project: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    level: Mapped[str] = mapped_column(String(10), nullable=False, default="error")
    triage: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    first_seen: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_seen: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    events_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SentryIssueEvent(Base):
    """Append-only, privacy-minimized audit of incident lifecycle decisions."""

    __tablename__ = "sentry_issue_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sentry_issues.id", ondelete="CASCADE"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    previous_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())


class SentryConnection(Base):
    """Conexión Sentry a nivel cuenta (1:1). El webhook_token enruta + autentica la
    entrada; client_secret habilita verificación HMAC y api_token la salida (feature B).
    Spec 2026-07-10."""

    __tablename__ = "sentry_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )
    webhook_token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    client_secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    api_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    org_slug: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    base_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
