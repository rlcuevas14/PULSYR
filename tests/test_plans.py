"""Hosted Free entitlements and self-hosted compatibility."""

import uuid

import pytest
from sqlalchemy import select

from app.accounts.members import create_member
from app.accounts.models import AccountSubscription
from app.accounts.plans import FREE, PlanLimitError, subscription_for
from app.accounts.service import create_account
from app.auth.models import User
from app.auth.service import authenticate, create_api_token, verify_api_token
from app.config import settings
from app.management.service import put_deliverable
from app.projects.service import create_project


async def _free_account(db):
    suffix = uuid.uuid4().hex[:8]
    account, owner = await create_account(
        db,
        f"Free {suffix}",
        f"free-{suffix}@test.cl",
        "Free Owner",
        None,
        plan_code=FREE,
    )
    await db.commit()
    return account, owner


@pytest.mark.asyncio
async def test_account_provisioning_rolls_back_as_one_unit(db):
    email = f"rollback-{uuid.uuid4().hex[:8]}@test.cl"
    account, _owner = await create_account(
        db, "Rollback", email, "Owner", None, plan_code=FREE
    )
    await create_project(db, name="Starter", account_id=account.id)
    account_id = account.id
    await db.rollback()

    assert await db.scalar(select(User.id).where(User.email == email)) is None
    assert await db.scalar(
        select(AccountSubscription.id).where(AccountSubscription.account_id == account_id)
    ) is None


@pytest.mark.asyncio
async def test_free_plan_limits_active_projects(db):
    account, _owner = await _free_account(db)
    await create_project(db, name="First", account_id=account.id)
    await db.commit()

    with pytest.raises(PlanLimitError, match="projects"):
        await create_project(db, name="Second", account_id=account.id)


@pytest.mark.asyncio
async def test_self_hosted_accounts_remain_unlimited(db):
    suffix = uuid.uuid4().hex[:8]
    account, _owner = await create_account(
        db, f"Private {suffix}", f"private-{suffix}@test.cl", "Owner", "password123"
    )
    await create_project(db, name="One", account_id=account.id)
    await create_project(db, name="Two", account_id=account.id)
    await db.commit()


@pytest.mark.asyncio
async def test_free_plan_limits_members_and_project_tokens(db):
    account, owner = await _free_account(db)
    project = await create_project(db, name="Starter", account_id=account.id)
    await db.commit()

    await create_member(db, account.id, f"member-{uuid.uuid4().hex[:6]}@test.cl", "M", "password123")
    with pytest.raises(PlanLimitError, match="members"):
        await create_member(
            db,
            account.id,
            f"member-{uuid.uuid4().hex[:6]}@test.cl",
            "M2",
            "password123",
        )

    for number in range(settings.free_max_tokens_per_project):
        await create_api_token(
            db, f"token-{number}", "write", owner.id, project_id=project.id
        )
    with pytest.raises(PlanLimitError, match="tokens"):
        await create_api_token(db, "one-too-many", "write", owner.id, project_id=project.id)


@pytest.mark.asyncio
async def test_free_plan_limits_document_storage(db, monkeypatch):
    account, owner = await _free_account(db)
    project = await create_project(db, name="Storage", account_id=account.id)
    await db.commit()
    monkeypatch.setattr(settings, "free_max_storage_mb", 1)

    await put_deliverable(
        db,
        project.id,
        compartment_name="Docs",
        name="first",
        doc_type="md",
        content=b"a" * 700_000,
        actor=owner.email,
    )
    await db.commit()
    with pytest.raises(PlanLimitError, match="storage"):
        await put_deliverable(
            db,
            project.id,
            compartment_name="Docs",
            name="second",
            doc_type="md",
            content=b"b" * 400_000,
            actor=owner.email,
        )


@pytest.mark.asyncio
async def test_suspended_subscription_cannot_authenticate(db):
    suffix = uuid.uuid4().hex[:8]
    account, owner = await create_account(
        db,
        f"Suspended {suffix}",
        f"suspended-{suffix}@test.cl",
        "Owner",
        "password123",
        plan_code=FREE,
    )
    await db.commit()
    project = await create_project(db, name="Suspended", account_id=account.id)
    _token, raw = await create_api_token(
        db, "before-suspension", "write", owner.id, project_id=project.id
    )
    subscription = await subscription_for(db, account.id)
    assert subscription is not None
    subscription.status = "suspended"
    await db.commit()

    assert await authenticate(db, owner.email, "password123") is None
    assert await verify_api_token(db, raw) is None
