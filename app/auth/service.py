import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import ApiToken, User

# ARCH-1/PERF-05: we only refresh last_used_at when this interval has passed since the
# last use (throttle). Avoids one UPDATE per request — the value means "recent activity",
# not a precise audit log.
_LAST_USED_THROTTLE = timedelta(minutes=5)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(
        select(User).where(User.email == email, User.is_active.is_(True))
    )
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active.is_(True))
    )
    return result.scalar_one_or_none()


async def authenticate(db: AsyncSession, email: str, password: str) -> User | None:
    user = await get_user_by_email(db, email)
    if user is None:
        return None
    # An OAuth-only user has no password. Refuse rather than fall through, so a
    # NULL hash can never be coaxed into matching.
    if user.password_hash is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def create_user(
    db: AsyncSession,
    email: str,
    name: str,
    password: str,
    role: str = "viewer",
    *,
    account_id: uuid.UUID | None = None,
    account_role: str | None = None,
    is_superadmin: bool | None = None,
) -> User:
    """Create a user.

    If ``account_id`` is omitted, a personal account is created and the user becomes
    its owner — convenient for tests and simple flows. The legacy ``role`` arg maps to
    account semantics: ``"admin"`` -> owner + superadmin, anything else -> member.
    """
    auto_account = account_id is None
    if auto_account:
        from app.accounts.models import Account
        from app.accounts.service import _slugify, _unique_slug

        acc = Account(name=name or email, slug=await _unique_slug(db, _slugify(name or email)))
        db.add(acc)
        await db.flush()
        account_id = acc.id
    if account_role is None:
        account_role = "owner" if role == "admin" else "member"
    if is_superadmin is None:
        is_superadmin = role == "admin"
    user = User(
        email=email,
        name=name,
        password_hash=hash_password(password),
        account_id=account_id,
        account_role=account_role,
        is_superadmin=is_superadmin,
    )
    db.add(user)
    if auto_account:
        # A personal account gets a starter project so the user is immediately usable.
        assert account_id is not None
        from app.projects.service import create_project

        await create_project(db, name="Default", account_id=account_id)
    await db.commit()
    await db.refresh(user)
    return user


async def create_api_token(
    db: AsyncSession, name: str, scopes: str, created_by: uuid.UUID
) -> tuple[ApiToken, str]:
    raw = secrets.token_urlsafe(32)
    token = ApiToken(
        name=name,
        token_hash=_hash_token(raw),
        scopes=scopes,
        created_by=created_by,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)
    return token, raw


async def verify_api_token(db: AsyncSession, raw: str) -> ApiToken | None:
    """Resolve a live Bearer token to its ApiToken row (or None).

    SEC-03: discards revoked tokens (revoked_at IS NOT NULL) and expired ones
    (expires_at <= now()). A token without expires_at never expires.

    ARCH-1/PERF-05: refreshes last_used_at THROTTLED (at most once every
    5 min) and WITHOUT a commit of its own. It used to do UPDATE + commit() in the
    middle of the request, which (a) broke the atomicity of the caller's transaction
    — a commit here half-persists any in-flight writes of the request — and (b)
    produced a write on every read. Now we only mutate the ORM attribute in memory;
    the request's own commit (the write routers and the MCP endpoint already commit)
    persists it. On read-only requests the refresh may not be persisted: that is
    acceptable, last_used_at is best-effort by design.
    """
    now = datetime.now(timezone.utc)
    hashed = _hash_token(raw)
    result = await db.execute(
        select(ApiToken).where(
            ApiToken.token_hash == hashed,
            ApiToken.revoked_at.is_(None),
            or_(ApiToken.expires_at.is_(None), ApiToken.expires_at > now),
        )
    )
    token = result.scalar_one_or_none()
    if token is not None and (token.last_used_at is None or token.last_used_at < now - _LAST_USED_THROTTLE):
        # In-memory mutation, no commit: the request's commit persists it (best-effort).
        token.last_used_at = now
    return token


async def revoke_api_token(db: AsyncSession, token_id: uuid.UUID) -> None:
    await db.execute(
        update(ApiToken)
        .where(ApiToken.id == token_id)
        .values(revoked_at=datetime.now(timezone.utc))
    )
    await db.commit()
