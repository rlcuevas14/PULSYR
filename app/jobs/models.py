import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import JSON, TIMESTAMP, CheckConstraint, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.enums import AGENT_RUN_KINDS, AGENT_RUN_STATUSES, check_in


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(check_in("kind", AGENT_RUN_KINDS), name="agent_runs_kind_check"),
        CheckConstraint(
            check_in("status", AGENT_RUN_STATUSES),
            name="agent_runs_status_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    ref_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    ref_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    leased_until: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    result: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tokens_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
