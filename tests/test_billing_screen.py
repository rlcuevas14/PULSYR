"""The billing screen: who may see it, and what it is allowed to claim."""

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from app.accounts.plans import FREE, SELF_HOSTED, apply_paddle_subscription
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


async def _paid_owner_account(db, plan_code="solo"):
    """An owner account with a real Paddle subscription id, the way the webhook
    would leave it, so the router's `subscription.paddle_subscription_id` guard
    actually opens and the live-detail path gets exercised."""
    account, owner = await _owner_account(db)
    await apply_paddle_subscription(
        db, account_id=account.id, plan_code=plan_code, paddle_status="active",
        subscription_id=f"sub_{uuid.uuid4().hex[:12]}", customer_id="ctm_x",
        occurred_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
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
    assert "0.0 MB" in r.text  # the storage usage figure for a brand-new account


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
    page instead of breaking it. Uses a paid subscription with a real
    paddle_subscription_id so the router's live-detail call, and therefore the
    monkeypatched failure, is actually reached."""
    from app.billing import paddle

    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    account, owner = await _paid_owner_account(db)
    await _login(client, owner.email)

    async def boom(_subscription_id: str):
        raise paddle.PaddleError("down")

    monkeypatch.setattr(paddle, "get_subscription", boom)
    r = await client.get("/billing")
    assert r.status_code == 200
    assert "Billing detail is temporarily unavailable." in r.text


@pytest.mark.asyncio
async def test_past_due_shows_banner_and_payment_link(client: AsyncClient, db, monkeypatch):
    """A past_due subscription (Paddle still retrying the card) shows the
    warning banner and a way to fix the card, without losing access."""
    from app.billing import paddle

    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    account, owner = await _paid_owner_account(db)
    await _login(client, owner.email)

    async def fake_get_subscription(_subscription_id: str) -> paddle.SubscriptionView:
        return paddle.SubscriptionView(
            "past_due", "pri_x", "solo", "monthly", None, None, None,
            "https://paddle.example/update", None,
        )

    monkeypatch.setattr(paddle, "get_subscription", fake_get_subscription)
    r = await client.get("/billing")
    assert r.status_code == 200
    assert "Your last payment failed" in r.text
    assert "https://paddle.example/update" in r.text


@pytest.mark.asyncio
async def test_scheduled_cancellation_shows_end_date_not_next_billing(
    client: AsyncClient, db, monkeypatch,
):
    """A subscription scheduled to cancel shows when access ends, not the
    misleading next-billing-date line."""
    from app.billing import paddle

    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    account, owner = await _paid_owner_account(db)
    await _login(client, owner.email)

    scheduled_at = datetime(2026, 9, 23, tzinfo=timezone.utc)
    next_billed_at = datetime(2026, 9, 1, tzinfo=timezone.utc)

    async def fake_get_subscription(_subscription_id: str) -> paddle.SubscriptionView:
        return paddle.SubscriptionView(
            "active", "pri_x", "solo", "monthly", next_billed_at, "cancel",
            scheduled_at, None, None,
        )

    monkeypatch.setattr(paddle, "get_subscription", fake_get_subscription)
    r = await client.get("/billing")
    assert r.status_code == 200
    assert "Your plan ends on" in r.text
    assert "Next billed on" not in r.text
