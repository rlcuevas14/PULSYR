"""Management (PMO) domain: documentos / plan / pendientes.

A new domain, orthogonal to the dev backlog: a project's classic PM surface.
UI is a viewer; the editor is Claude via MCP (the Gantt is MCP-edit only).

Isolation: every table carries `project_id` (nullable, like the rest of PULSYR —
isolation is enforced in code by app/projects/access.py + the MCP project failsafe,
not by a schema NOT NULL; a NULL project_id is an orphan invisible to every
project-scoped query). Audit: every mutation emits a ManagementEvent (append-only),
mirroring the ItemEvent guarantee of the backlog domain.
"""

import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.enums import (
    DELIVERABLE_STATUSES,
    DELIVERABLE_TYPES,
    PENDING_STATUSES,
    check_in,
)


class ManagementEvent(Base):
    """Audit primitive for the management domain (append-only).

    Generic on purpose (entity_type + entity_id, no FK) so one table covers all four
    entities. Every service mutation emits exactly one.
    """

    __tablename__ = "management_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())


class Compartment(Base):
    """A "compartimiento" — a flat folder grouping deliverables within a project."""

    __tablename__ = "compartments"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="compartments_project_name_uniq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    deliverables: Mapped[list["Deliverable"]] = relationship(
        "Deliverable", back_populates="compartment", order_by="Deliverable.name"
    )


class Deliverable(Base):
    """Logical identity of a deliverable (name + compartment + type). Content lives in
    append-only versions — re-uploading never overwrites history."""

    __tablename__ = "deliverables"
    __table_args__ = (
        CheckConstraint(check_in("doc_type", DELIVERABLE_TYPES), name="deliverables_doc_type_check"),
        CheckConstraint(check_in("status", DELIVERABLE_STATUSES), name="deliverables_status_check"),
        UniqueConstraint("compartment_id", "name", name="deliverables_compartment_name_uniq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    compartment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="draft")
    owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    summary_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    current_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    compartment: Mapped["Compartment"] = relationship("Compartment", back_populates="deliverables")
    versions: Mapped[list["DeliverableVersion"]] = relationship(
        "DeliverableVersion", back_populates="deliverable",
        order_by="DeliverableVersion.version_no", cascade="all, delete-orphan",
    )


class DeliverableVersion(Base):
    """Append-only content of a deliverable. Rollback = a new version copying an old one."""

    __tablename__ = "deliverable_versions"
    __table_args__ = (
        UniqueConstraint("deliverable_id", "version_no", name="deliverable_versions_no_uniq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deliverable_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deliverables.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    mime: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    deliverable: Mapped["Deliverable"] = relationship("Deliverable", back_populates="versions")


class Pending(Base):
    """A project action item: title + owner (free text) + simple status + optional due date.
    Deliberately NOT an Item — the dev backlog's 8-state lifecycle is too heavy for
    "call the client"; this keeps the backlog clean."""

    __tablename__ = "pendings"
    __table_args__ = (
        CheckConstraint(check_in("status", PENDING_STATUSES), name="pendings_status_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    detail_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="open")
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    plan_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plan_tasks.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class PlanTask(Base):
    """A row of the Gantt: 3-level hierarchy via self-ref parent. Rendered read-only as
    HTML/CSS; edited only via MCP. Phase (level-1) bars are a rollup of descendants when
    their own dates are null."""

    __tablename__ = "plan_tasks"
    __table_args__ = (
        CheckConstraint("progress >= 0 AND progress <= 100", name="plan_tasks_progress_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plan_tasks.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    progress: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    is_milestone: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deps: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
