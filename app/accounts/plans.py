"""Account plans and server-side entitlement enforcement.

The browser may explain a limit, but this module is the authority. Every mutation
that consumes a limited resource calls the corresponding guard before writing.
Accounts created before hosted signup have a self-hosted subscription; a missing
row also degrades to self-hosted so a partially migrated private install is never
accidentally locked out.
"""

import uuid
from dataclasses import dataclass
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


class PlanLimitError(ValueError):
    def __init__(self, resource: str, limit: int) -> None:
        self.resource = resource
        self.limit = limit
        super().__init__(f"Free plan limit reached for {resource} ({limit}).")


def limits_for(plan_code: str) -> PlanLimits:
    if plan_code == FREE:
        return PlanLimits(
            projects=settings.free_max_projects,
            members=settings.free_max_members,
            tokens_per_project=settings.free_max_tokens_per_project,
            storage_bytes=settings.free_max_storage_mb * 1024 * 1024,
        )
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


async def ensure_project_capacity(db: AsyncSession, account_id: uuid.UUID) -> None:
    limits = limits_for(await active_plan_code(db, account_id, for_update=True))
    if limits.projects is None:
        return
    from app.projects.models import Project

    used = await db.scalar(
        select(func.count()).select_from(Project).where(
            Project.account_id == account_id,
            Project.archived_at.is_(None),
        )
    )
    if (used or 0) >= limits.projects:
        raise PlanLimitError("projects", limits.projects)


async def ensure_member_capacity(db: AsyncSession, account_id: uuid.UUID) -> None:
    limits = limits_for(await active_plan_code(db, account_id, for_update=True))
    if limits.members is None:
        return
    from app.auth.models import User

    used = await db.scalar(
        select(func.count()).select_from(User).where(
            User.account_id == account_id,
            User.account_role == "member",
            User.is_active.is_(True),
        )
    )
    if (used or 0) >= limits.members:
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
    from app.management.models import Deliverable, DeliverableVersion
    from app.projects.models import Project

    used = await db.scalar(
        select(func.coalesce(func.sum(DeliverableVersion.size_bytes), 0))
        .select_from(DeliverableVersion)
        .join(Deliverable, Deliverable.id == DeliverableVersion.deliverable_id)
        .join(Project, Project.id == Deliverable.project_id)
        .where(Project.account_id == account_id)
    )
    if int(used or 0) + additional_bytes > limits.storage_bytes:
        raise PlanLimitError("storage", limits.storage_bytes)
