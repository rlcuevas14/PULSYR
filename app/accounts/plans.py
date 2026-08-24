"""Account plans and server-side entitlement enforcement.

The browser may explain a limit, but this module is the authority. Every mutation
that consumes a limited resource calls the corresponding guard before writing.
Accounts created before hosted signup have a self-hosted subscription; a missing
row also degrades to self-hosted so a partially migrated private install is never
accidentally locked out.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import AccountSubscription
from app.config import settings

FREE: Final = "free"
SELF_HOSTED: Final = "self_hosted"


@dataclass(frozen=True)
class PlanLimits:
    projects: int | None
    members: int | None
    tokens_per_project: int | None
    storage_bytes: int | None


@dataclass(frozen=True)
class Usage:
    projects: int
    members: int
    storage_bytes: int


class PlanLimitError(ValueError):
    def __init__(self, resource: str, limit: int) -> None:
        self.resource = resource
        self.limit = limit
        super().__init__(f"Free plan limit reached for {resource} ({limit}).")


# Paid tiers as published in the hosted Terms. Not settings: these numbers are a
# contractual promise to the customer, so the source of truth is the Terms table and
# a deployment must not be able to quietly tighten them.
# ponytail: Studio "unlimited" projects is unmetered per the fair-use clause; add a
# ceiling here only if unmetered turns out to cost real money.
PAID_LIMITS: Final[dict[str, PlanLimits]] = {
    "solo": PlanLimits(
        projects=5, members=3, tokens_per_project=None, storage_bytes=5 * 1024**3
    ),
    "studio": PlanLimits(
        projects=None, members=10, tokens_per_project=None, storage_bytes=25 * 1024**3
    ),
}


def limits_for(plan_code: str) -> PlanLimits:
    if plan_code == FREE:
        return PlanLimits(
            projects=settings.free_max_projects,
            members=settings.free_max_members,
            tokens_per_project=settings.free_max_tokens_per_project,
            storage_bytes=settings.free_max_storage_mb * 1024 * 1024,
        )
    if plan_code in PAID_LIMITS:
        return PAID_LIMITS[plan_code]
    return PlanLimits(projects=None, members=None, tokens_per_project=None, storage_bytes=None)


async def subscription_for(
    db: AsyncSession,
    account_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> AccountSubscription | None:
    query = select(AccountSubscription).where(AccountSubscription.account_id == account_id)
    if for_update:
        query = query.with_for_update()
    return await db.scalar(query)


async def active_plan_code(
    db: AsyncSession, account_id: uuid.UUID, *, for_update: bool = False
) -> str:
    subscription = await subscription_for(db, account_id, for_update=for_update)
    if subscription is None:
        return SELF_HOSTED
    if subscription.status != "active":
        raise PlanLimitError("subscription", 0)
    return subscription.plan_code


async def add_subscription(
    db: AsyncSession, account_id: uuid.UUID, plan_code: str
) -> AccountSubscription:
    subscription = AccountSubscription(
        account_id=account_id,
        plan_code=plan_code,
        status="active",
    )
    db.add(subscription)
    await db.flush()
    return subscription


# Paddle's subscription statuses collapsed onto the three this table already has.
# `past_due` keeps access on purpose: Paddle is still retrying the card, and locking
# someone out mid-dunning loses the customer we are trying to recover.
PADDLE_STATUS: Final[dict[str, str]] = {
    "active": "active",
    "trialing": "active",
    "past_due": "active",
    "paused": "suspended",
    "canceled": "canceled",
}


async def apply_paddle_subscription(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    plan_code: str,
    paddle_status: str,
    subscription_id: str,
    customer_id: str | None,
    occurred_at: datetime,
) -> str:
    """Write the plan a Paddle event describes. Returns what was done, for the log.

    A cancellation that has taken effect drops the account to Free rather than to a
    dead `canceled` row: the Terms promise we never delete data over a quota, and Free
    is the plan that keeps the account readable and writable within smaller limits.
    """
    status = PADDLE_STATUS.get(paddle_status)
    if status is None:
        return "ignored:unknown_status"
    if status == "canceled":
        plan_code, status = FREE, "active"

    subscription = await subscription_for(db, account_id, for_update=True)
    if subscription is None:
        subscription = AccountSubscription(account_id=account_id, plan_code=plan_code)
        db.add(subscription)
    elif subscription.paddle_event_at is not None and (
        subscription.paddle_event_at >= occurred_at
    ):
        return "ignored:stale_event"

    subscription.plan_code = plan_code
    subscription.status = status
    subscription.paddle_subscription_id = subscription_id
    subscription.paddle_customer_id = customer_id
    subscription.paddle_event_at = occurred_at
    await db.flush()
    return f"applied:{plan_code}/{status}"


async def count_projects(db: AsyncSession, account_id: uuid.UUID) -> int:
    from app.projects.models import Project

    return int(await db.scalar(
        select(func.count()).select_from(Project).where(
            Project.account_id == account_id,
            Project.archived_at.is_(None),
        )
    ) or 0)


async def count_members(db: AsyncSession, account_id: uuid.UUID) -> int:
    from app.auth.models import User

    return int(await db.scalar(
        select(func.count()).select_from(User).where(
            User.account_id == account_id,
            User.account_role == "member",
            User.is_active.is_(True),
        )
    ) or 0)


async def used_storage_bytes(db: AsyncSession, account_id: uuid.UUID) -> int:
    from app.management.models import Deliverable, DeliverableVersion
    from app.projects.models import Project

    return int(await db.scalar(
        select(func.coalesce(func.sum(DeliverableVersion.size_bytes), 0))
        .select_from(DeliverableVersion)
        .join(Deliverable, Deliverable.id == DeliverableVersion.deliverable_id)
        .join(Project, Project.id == Deliverable.project_id)
        .where(Project.account_id == account_id)
    ) or 0)


async def usage_for(db: AsyncSession, account_id: uuid.UUID) -> Usage:
    return Usage(
        projects=await count_projects(db, account_id),
        members=await count_members(db, account_id),
        storage_bytes=await used_storage_bytes(db, account_id),
    )


async def ensure_project_capacity(db: AsyncSession, account_id: uuid.UUID) -> None:
    limits = limits_for(await active_plan_code(db, account_id, for_update=True))
    if limits.projects is None:
        return
    if await count_projects(db, account_id) >= limits.projects:
        raise PlanLimitError("projects", limits.projects)


async def ensure_member_capacity(db: AsyncSession, account_id: uuid.UUID) -> None:
    limits = limits_for(await active_plan_code(db, account_id, for_update=True))
    if limits.members is None:
        return
    if await count_members(db, account_id) >= limits.members:
        raise PlanLimitError("members", limits.members)


async def ensure_token_capacity(
    db: AsyncSession, account_id: uuid.UUID, project_id: uuid.UUID
) -> None:
    limits = limits_for(await active_plan_code(db, account_id, for_update=True))
    if limits.tokens_per_project is None:
        return
    from app.auth.models import ApiToken

    used = await db.scalar(
        select(func.count()).select_from(ApiToken).where(
            ApiToken.project_id == project_id,
            ApiToken.revoked_at.is_(None),
        )
    )
    if (used or 0) >= limits.tokens_per_project:
        raise PlanLimitError("tokens", limits.tokens_per_project)


async def ensure_storage_capacity(
    db: AsyncSession, account_id: uuid.UUID, additional_bytes: int
) -> None:
    limits = limits_for(await active_plan_code(db, account_id, for_update=True))
    if limits.storage_bytes is None:
        return
    if await used_storage_bytes(db, account_id) + additional_bytes > limits.storage_bytes:
        raise PlanLimitError("storage", limits.storage_bytes)
