import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, CheckConstraint, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.enums import PLAN_CODES, SUBSCRIPTION_STATUSES, check_in


class Account(Base):
    """A tenant: an isolated set of projects owned by one account-owner."""

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    subscription: Mapped["AccountSubscription"] = relationship(
        "AccountSubscription", back_populates="account", uselist=False, lazy="selectin"
    )


class AccountSubscription(Base):
    """The product tier and lifecycle attached to one tenant.

    Billing-provider identifiers deliberately do not live here yet: the Free and
    self-hosted tiers do not need a payment processor, while the stable account-level
    contract below leaves room for a paid provider integration later.
    """

    __tablename__ = "account_subscriptions"
    __table_args__ = (
        UniqueConstraint("account_id", name="account_subscriptions_account_uniq"),
        CheckConstraint(check_in("plan_code", PLAN_CODES), name="account_subscriptions_plan_check"),
        CheckConstraint(
            check_in("status", SUBSCRIPTION_STATUSES),
            name="account_subscriptions_status_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    plan_code: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    account: Mapped["Account"] = relationship("Account", back_populates="subscription")
