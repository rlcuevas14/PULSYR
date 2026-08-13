import re
import unicodedata
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.accounts.models import Account
from app.accounts.plans import SELF_HOSTED, add_subscription
from app.auth.models import User
from app.auth.service import hash_password
from app.enums import PLAN_CODES, SUBSCRIPTION_STATUSES


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
    password: str | None,
    *,
    is_superadmin: bool = False,
    plan_code: str = SELF_HOSTED,
) -> tuple[Account, User]:
    """Stage an account, its owner and subscription in the caller's transaction.

    Reusable, as designed: the setup wizard and the super-admin panel pass a
    password; the public OAuth signup passes None, because the provider holds the
    credential and this side stores no secret for that user. The caller owns the
    commit so it can atomically add the initial project, token or OAuth identity.
    """
    name = name.strip()
    if not name:
        raise AccountError("Account name cannot be empty.")
    owner_email = owner_email.strip().casefold()
    if not owner_email:
        raise AccountError("Owner email cannot be empty.")
    if password is not None and len(password) < 8:
        raise AccountError("Password must be at least 8 characters.")
    if await db.scalar(select(User.id).where(func.lower(User.email) == owner_email)):
        raise AccountError("A user with that email already exists.")

    acc = Account(name=name, slug=await _unique_slug(db, _slugify(name)))
    db.add(acc)
    await db.flush()
    owner = User(
        email=owner_email,
        name=owner_name,
        password_hash=hash_password(password) if password is not None else None,
        account_id=acc.id,
        account_role="owner",
        is_superadmin=is_superadmin,
    )
    db.add(owner)
    await db.flush()
    await add_subscription(db, acc.id, plan_code)
    return acc, owner


async def list_accounts(db: AsyncSession) -> list[Account]:
    result = await db.execute(
        select(Account)
        .options(selectinload(Account.subscription))
        .order_by(Account.created_at.desc())
    )
    return list(result.scalars().all())


async def set_account_active(db: AsyncSession, account_id: uuid.UUID, active: bool) -> None:
    acc = await db.get(Account, account_id)
    if acc is not None:
        acc.is_active = active
        await db.commit()


async def update_subscription(
    db: AsyncSession,
    account_id: uuid.UUID,
    *,
    plan_code: str,
    status: str,
) -> None:
    if plan_code not in PLAN_CODES or status not in SUBSCRIPTION_STATUSES:
        raise AccountError("Invalid subscription plan or status.")
    from app.accounts.plans import subscription_for

    subscription = await subscription_for(db, account_id, for_update=True)
    if subscription is None:
        subscription = await add_subscription(db, account_id, plan_code)
    subscription.plan_code = plan_code
    subscription.status = status
    await db.commit()
