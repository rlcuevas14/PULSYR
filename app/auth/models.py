import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import TIMESTAMP, Boolean, CheckConstraint, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.enums import ACCOUNT_ROLES, TOKEN_SCOPES, check_in


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(check_in("account_role", ACCOUNT_ROLES), name="users_account_role_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    account_role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    is_superadmin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # NULL for users who signed in with GitHub/Google: they never had a password
    # here, so there is nothing to store and nothing to reset.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tokens: Mapped[list["ApiToken"]] = relationship("ApiToken", back_populates="creator")


class ApiToken(Base):
    __tablename__ = "api_tokens"
    __table_args__ = (
        CheckConstraint(check_in("scopes", TOKEN_SCOPES), name="api_tokens_scopes_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scopes: Mapped[str] = mapped_column(String(20), nullable=False, default="read")
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    # SEC-03: expiración opcional del token (NULL = sin expiración).
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    creator: Mapped["User"] = relationship("User", back_populates="tokens")
