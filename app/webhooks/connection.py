"""Account-level Sentry connection: token routing, config, re-attach of unmatched rows.

Tenancy chokepoint for webhooks (spec 2026-07-10 §4.2): every lookup here is scoped
by account_id, so a webhook token can only write within its own account.
"""

import re
import secrets
import uuid

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.projects.models import Project
from app.secret_fields import resolve_write_only_secret
from app.webhooks.models import SentryConnection, SentryIssue

DEFAULT_BASE_URL = "https://sentry.io"
# Only scheme://host[:port] — no path/query (mirrors Sentry's system.url-prefix rule).
_BASE_URL_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d+)?$")


class SentryConfigError(ValueError):
    pass


async def get_by_token(db: AsyncSession, token: str) -> SentryConnection | None:
    return (await db.execute(
        select(SentryConnection).where(SentryConnection.webhook_token == token)
    )).scalar_one_or_none()


async def get_for_account(db: AsyncSession, account_id: uuid.UUID) -> SentryConnection | None:
    return (await db.execute(
        select(SentryConnection).where(SentryConnection.account_id == account_id)
    )).scalar_one_or_none()


async def get_or_create(db: AsyncSession, account_id: uuid.UUID) -> SentryConnection:
    conn = await get_for_account(db, account_id)
    if conn is None:
        conn = SentryConnection(account_id=account_id, webhook_token=secrets.token_urlsafe(32))
        db.add(conn)
        await db.flush()
    return conn


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


async def update_connection(
    db: AsyncSession, conn: SentryConnection, *,
    client_secret: str | None, api_token: str | None,
    org_slug: str | None, base_url: str | None,
    clear_client_secret: bool = False, clear_api_token: bool = False,
) -> SentryConnection:
    base = _clean(base_url)
    if base and not _BASE_URL_RE.match(base):
        raise SentryConfigError("base_url must be http(s)://host[:port] with no path.")
    conn.client_secret = resolve_write_only_secret(
        conn.client_secret, client_secret, clear=clear_client_secret
    )
    conn.api_token = resolve_write_only_secret(conn.api_token, api_token, clear=clear_api_token)
    conn.org_slug = _clean(org_slug)
    conn.base_url = base
    await db.flush()
    return conn


async def regenerate_token(db: AsyncSession, conn: SentryConnection) -> SentryConnection:
    conn.webhook_token = secrets.token_urlsafe(32)
    await db.flush()
    return conn


def effective_base_url(conn: SentryConnection | None) -> str:
    return conn.base_url if (conn and conn.base_url) else DEFAULT_BASE_URL


async def outbound(db: AsyncSession, account_id: uuid.UUID | None) -> SentryConnection | None:
    """Connection usable for outbound calls to the Sentry API (feature B), or None."""
    if account_id is None:
        return None
    conn = await get_for_account(db, account_id)
    return conn if (conn and conn.api_token) else None


async def route_project(
    db: AsyncSession, account_id: uuid.UUID, slug: str | None
) -> Project | None:
    """slug → project, scoped to the account. slug=None only routes when the account
    has exactly one mapped project (event_alert payloads carry no slug)."""
    if slug:
        return (await db.execute(
            select(Project).where(
                Project.account_id == account_id, Project.sentry_project_slug == slug
            )
        )).scalar_one_or_none()
    mapped = list((await db.execute(
        select(Project).where(
            Project.account_id == account_id, Project.sentry_project_slug.is_not(None)
        ).limit(2)
    )).scalars().all())
    return mapped[0] if len(mapped) == 1 else None


def _unmatched_filter(account_id: uuid.UUID):
    return (
        SentryIssue.project_id.is_(None),
        or_(SentryIssue.account_id == account_id, SentryIssue.account_id.is_(None)),
    )


async def count_unmatched(db: AsyncSession, account_id: uuid.UUID) -> int:
    return int(await db.scalar(
        select(func.count()).select_from(SentryIssue).where(*_unmatched_filter(account_id))
    ) or 0)


async def reattach_unmatched(db: AsyncSession, account_id: uuid.UUID) -> int:
    """Attach project-less rows to this account's projects by their text slug.
    Rows without an account (single-account era, pre-v0017) are claimable; rows from
    other accounts never are."""
    mapped = (await db.execute(
        select(Project.id, Project.sentry_project_slug).where(
            Project.account_id == account_id, Project.sentry_project_slug.is_not(None)
        )
    )).all()
    total = 0
    for pid, slug in mapped:
        res = await db.execute(
            update(SentryIssue)
            .where(*_unmatched_filter(account_id), SentryIssue.project == slug)
            .values(project_id=pid, account_id=account_id)
        )
        total += int(getattr(res, "rowcount", 0) or 0)  # CursorResult; stub-safe in CI
    await db.flush()
    return total
