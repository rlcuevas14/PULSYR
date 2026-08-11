import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.enums import (
    COMMENT_KINDS,
    EFFORTS,
    ITEM_STATUSES,
    ITEM_TYPES,
    ORIGENES,
    PRIORITIES,
    RELATIONS,
    check_in,
)


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        CheckConstraint(check_in("type", ITEM_TYPES), name="items_type_check"),
        CheckConstraint(check_in("status", ITEM_STATUSES), name="items_status_check"),
        CheckConstraint(
            f"priority IS NULL OR {check_in('priority', PRIORITIES)}",
            name="items_priority_check",
        ),
        CheckConstraint(
            f"effort_ai IS NULL OR {check_in('effort_ai', EFFORTS)}",
            name="items_effort_ai_check",
        ),
        CheckConstraint(check_in("origen", ORIGENES), name="items_origen_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    scope_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scopes.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="backlog")
    priority: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    effort_ai: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    impact_ai: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    impact_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    effort_declared: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    priority_declared: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    trigger_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dependencies: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    origen: Mapped[str] = mapped_column(String(20), nullable=False, default="human")
    source_refs: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    stale_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    agent_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # Tocado por un push/sesión (webhook Git / pulsyr_completar). Distinto de updated_at
    # (que dispara con cualquier UPDATE de la fila).
    last_touched_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # FK al Hilo que originó este ítem (Sprint 4). Nullable: la mayoría de ítems no son de un hilo.
    thread_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("threads.id", ondelete="SET NULL"), nullable=True
    )

    # embedding: vector(768) solo existe en la BD, no en el ORM (se gestiona raw en F2)

    comments: Mapped[list["ItemComment"]] = relationship("ItemComment", back_populates="item")
    events: Mapped[list["ItemEvent"]] = relationship("ItemEvent", back_populates="item")
    enrichments: Mapped[list["AiEnrichment"]] = relationship("AiEnrichment", back_populates="item")


class ItemRelationship(Base):
    """Arco tipado del grafo entre dos ítems. El grafo se construye incrementalmente."""

    __tablename__ = "item_relationships"
    __table_args__ = (
        CheckConstraint(
            check_in("relation", RELATIONS),
            name="item_relationships_relation_check",
        ),
        CheckConstraint("source_id <> target_id", name="item_rel_no_self"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    # DB es TEXT (v0003); el ORM debe coincidir (DM-09).
    relation: Mapped[str] = mapped_column(Text, primary_key=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())


class ItemComment(Base):
    __tablename__ = "item_comments"
    __table_args__ = (
        CheckConstraint(check_in("kind", COMMENT_KINDS), name="item_comments_kind_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="comment")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    item: Mapped["Item"] = relationship("Item", back_populates="comments")


class ItemEvent(Base):
    __tablename__ = "item_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    item: Mapped["Item"] = relationship("Item", back_populates="events")


class AiEnrichment(Base):
    __tablename__ = "ai_enrichments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False)
    effort: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    impact: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(3, 2), nullable=True)
    duplicates: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    tokens_in: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    item: Mapped["Item"] = relationship("Item", back_populates="enrichments")
