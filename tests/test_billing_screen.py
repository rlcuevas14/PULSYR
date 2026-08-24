"""The billing screen: who may see it, and what it is allowed to claim."""

import uuid

import pytest
from httpx import AsyncClient

from app.accounts.plans import FREE, SELF_HOSTED
from app.accounts.service import create_account
from app.config import settings


async def _owner_account(db, plan_code=FREE):
    suffix = uuid.uuid4().hex[:8]
    account, owner = await create_account(
        db, f"Bill {suffix}", f"bill-{suffix}@test.cl", "Owner", "secret-password",
        plan_code=plan_code,
    )
    await db.commit()
    return account, owner


async def _login(client: AsyncClient, email: str) -> None:
    await client.post("/login", data={"email": email, "password": "secret-password"})


@pytest.mark.asyncio
async def test_owner_sees_plan_and_usage(client: AsyncClient, db, monkeypatch):
    monkeypatch.setattr(settings, "paddle_api_key", "")
    account, owner = await _owner_account(db)
    await _login(client, owner.email)

    r = await client.get("/billing")
    assert r.status_code == 200
    assert "Free" in r.text or "Gratuito" in r.text


@pytest.mark.asyncio
async def test_self_hosted_has_no_billing_screen(client: AsyncClient, db, monkeypatch):
    """A self-hosted install must not show a page about paying us."""
    monkeypatch.setattr(settings, "paddle_api_key", "")
    account, owner = await _owner_account(db, plan_code=SELF_HOSTED)
    await _login(client, owner.email)

    assert (await client.get("/billing")).status_code == 404


@pytest.mark.asyncio
async def test_member_cannot_reach_billing(client: AsyncClient, db, monkeypatch):
    """Billing belongs to the account holder, not to a collaborator with
    editor grants on every project."""
    from app.accounts.members import create_member

    monkeypatch.setattr(settings, "paddle_api_key", "")
    account, _owner = await _owner_account(db)
    member = await create_member(
        db, account_id=account.id, email=f"m-{uuid.uuid4().hex[:6]}@test.cl",
        name="Member", password="secret-password",
    )
    await db.commit()
    await _login(client, member.email)

    assert (await client.get("/billing")).status_code in (403, 404)


@pytest.mark.asyncio
async def test_screen_renders_when_paddle_is_unreachable(client: AsyncClient, db, monkeypatch):
    """Plan and usage come from the mirror, so an outage at Paddle degrades the
    page instead of breaking it."""
    from app.billing import paddle

    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    account, owner = await _owner_account(db)
    await _login(client, owner.email)

    async def boom(_subscription_id: str):
        raise paddle.PaddleError("down")

    monkeypatch.setattr(paddle, "get_subscription", boom)
    r = await client.get("/billing")
    assert r.status_code == 200
