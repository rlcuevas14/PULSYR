import re
import unicodedata
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account
from app.auth.models import User
from app.auth.service import hash_password


class AccountError(Exception):
    """Raised on invalid account/owner creation input."""


def _slugify(name: str) -> str:
    norm = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")
    return slug or "account"


async def _unique_slug(db: AsyncSession, base: str) -> str:
    slug = base
    while await db.scalar(select(Account.id).where(Account.slug == slug)):
        slug = f"{base}-{uuid.uuid4().hex[:4]}"
    return slug


async def create_account(
    db: AsyncSession,
    name: str,
    owner_email: str,
    owner_name: str,
    password: str,
    *,
    is_superadmin: bool = False,
) -> tuple[Account, User]:
    """Create an account and its owner user in one transaction.

    Reusable: the super-admin panel calls this today; a future public /signup
    (pulsyr.io) calls the same function — no rearchitecture.
    """
    name = name.strip()
    if not name:
        raise AccountError("Account name cannot be empty.")
    if not owner_email.strip():
        raise AccountError("Owner email cannot be empty.")
    if len(password) < 8:
        raise AccountError("Password must be at least 8 characters.")
    if await db.scalar(select(User.id).where(User.email == owner_email)):
        raise AccountError("A user with that email already exists.")

    acc = Account(name=name, slug=await _unique_slug(db, _slugify(name)))
    db.add(acc)
    await db.flush()
    owner = User(
        email=owner_email,
        name=owner_name,
        password_hash=hash_password(password),
        account_id=acc.id,
        account_role="owner",
        is_superadmin=is_superadmin,
    )
    db.add(owner)
    await db.commit()
    await db.refresh(acc)
    await db.refresh(owner)
    return acc, owner


async def list_accounts(db: AsyncSession) -> list[Account]:
    result = await db.execute(select(Account).order_by(Account.created_at.desc()))
    return list(result.scalars().all())


async def set_account_active(db: AsyncSession, account_id: uuid.UUID, active: bool) -> None:
    acc = await db.get(Account, account_id)
    if acc is not None:
        acc.is_active = active
        await db.commit()
