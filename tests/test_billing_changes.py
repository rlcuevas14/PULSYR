"""Plan changes: what gets charged, who may ask, and what the screen may claim."""

import uuid

import pytest
from httpx import AsyncClient

from app.accounts.plans import FREE
from app.accounts.service import create_account
from app.billing import paddle, service
from app.config import settings


def _price(plan_code, billing_period, price_id="pri_x"):
    return paddle.PlanPrice(price_id, plan_code, billing_period, "800", "USD")


def test_tier_upgrade_charges_the_difference_now():
    """More capacity now means paying for it now."""
    current = _price("solo", "monthly")
    target = _price("studio", "monthly")
    assert service.proration_for(current, target) == paddle.PRORATION_UPGRADE


def test_tier_downgrade_credits_at_renewal():
    """Never refund mid-period for a downgrade: credit it at renewal."""
    assert service.proration_for(
        _price("studio", "monthly"), _price("solo", "monthly")
    ) == paddle.PRORATION_DOWNGRADE


def test_monthly_to_yearly_charges_now():
    """A year is a much larger payment; charging now is the honest moment."""
    assert service.proration_for(
        _price("solo", "monthly"), _price("solo", "yearly")
    ) == paddle.PRORATION_UPGRADE


def test_yearly_to_monthly_waits_for_renewal():
    """Let the paid year run out rather than unwinding it."""
    assert service.proration_for(
        _price("solo", "yearly"), _price("solo", "monthly")
    ) == paddle.PRORATION_DOWNGRADE


def test_tier_change_wins_over_term_change():
    """Studio yearly to Solo monthly is a downgrade even though the term also
    shortens: the smaller of the two must not be charged immediately."""
    assert service.proration_for(
        _price("studio", "yearly"), _price("solo", "monthly")
    ) == paddle.PRORATION_DOWNGRADE


def test_tier_upgrade_overrides_term_downgrade():
    """Solo yearly to Studio monthly is an upgrade: tier wins even when the
    term shortens. This proves tier is checked before term in the decision."""
    assert service.proration_for(
        _price("solo", "yearly"), _price("studio", "monthly")
    ) == paddle.PRORATION_UPGRADE


def test_unknown_plan_code_credits_at_renewal():
    """When Paddle omits items from the subscription response, plan_code is
    empty. Never charge on a guess; credit at renewal instead."""
    current = paddle.SubscriptionView(
        status="active",
        price_id="pri_current",
        plan_code="",
        billing_period="monthly",
        next_billed_at=None,
        scheduled_action=None,
        scheduled_at=None,
        update_payment_method_url=None,
        cancel_url=None,
    )
    target = _price("studio", "monthly")
    assert service.proration_for(current, target) == paddle.PRORATION_DOWNGRADE


async def _paid_owner(db, monkeypatch, plan_code="solo"):
    """An account whose mirror already carries a Paddle subscription."""
    from datetime import datetime, timezone

    from app.accounts.plans import apply_paddle_subscription

    suffix = uuid.uuid4().hex[:8]
    account, owner = await create_account(
        db, f"Chg {suffix}", f"chg-{suffix}@test.cl", "Owner", "secret-password",
        plan_code=FREE,
    )
    await apply_paddle_subscription(
        db, account_id=account.id, plan_code=plan_code, paddle_status="active",
        subscription_id=f"sub_{suffix}", customer_id="ctm_x",
        occurred_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    await db.commit()
    return account, owner


@pytest.mark.asyncio
async def test_confirmation_shows_paddle_figures(client: AsyncClient, db, monkeypatch):
    """The amount on screen is Paddle's, never one we computed."""
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    _account, owner = await _paid_owner(db, monkeypatch)

    async def prices():
        return [_price("studio", "monthly", "pri_studio_m")]

    async def current_subscription(_subscription_id: str) -> paddle.SubscriptionView:
        # What the account is on today, read live from Paddle: this is what
        # proration_for compares the target against, independent of the local
        # mirror's plan_code.
        return paddle.SubscriptionView(
            status="active", price_id="pri_solo_m", plan_code="solo",
            billing_period="monthly", next_billed_at=None, scheduled_action=None,
            scheduled_at=None, update_payment_method_url=None, cancel_url=None,
        )

    async def preview(subscription_id, price_id, proration):
        assert proration == paddle.PRORATION_UPGRADE
        return paddle.ChangePreview("1240", "2000", "USD", None)

    monkeypatch.setattr(paddle, "list_plan_prices", prices)
    monkeypatch.setattr(paddle, "get_subscription", current_subscription)
    monkeypatch.setattr(paddle, "preview_change", preview)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.get("/ui/billing/confirm?price_id=pri_studio_m")
    assert r.status_code == 200
    assert "12.40" in r.text
    assert "20.00" in r.text


@pytest.mark.asyncio
async def test_confirmation_rejects_a_price_outside_the_catalog(client: AsyncClient, db, monkeypatch):
    """A hand-edited price id must not reach Paddle."""
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    _account, owner = await _paid_owner(db, monkeypatch)

    async def prices():
        return [_price("studio", "monthly", "pri_studio_m")]

    monkeypatch.setattr(paddle, "list_plan_prices", prices)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.get("/ui/billing/confirm?price_id=pri_someone_elses")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_confirmation_shows_downgrade_warning_and_no_charge_today(
    client: AsyncClient, db, monkeypatch,
):
    """A downgrade must show the capacity-drops-now warning and must not claim a
    charge today: Paddle reports no immediate transaction for a downgrade. The
    proration mode is not hardcoded here; it is left for the route to derive from
    proration_for and pass through to preview_change."""
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    _account, owner = await _paid_owner(db, monkeypatch, plan_code="studio")

    async def prices():
        return [_price("solo", "monthly", "pri_solo_m")]

    async def current_subscription(_subscription_id: str) -> paddle.SubscriptionView:
        return paddle.SubscriptionView(
            status="active", price_id="pri_studio_m", plan_code="studio",
            billing_period="monthly", next_billed_at=None, scheduled_action=None,
            scheduled_at=None, update_payment_method_url=None, cancel_url=None,
        )

    async def preview(subscription_id, price_id, proration):
        return paddle.ChangePreview(None, "800", "USD", None)

    monkeypatch.setattr(paddle, "list_plan_prices", prices)
    monkeypatch.setattr(paddle, "get_subscription", current_subscription)
    monkeypatch.setattr(paddle, "preview_change", preview)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.get("/ui/billing/confirm?price_id=pri_solo_m")
    assert r.status_code == 200
    assert "Your new limits apply immediately. The difference is credited to your next renewal." in r.text
    assert "No charge today." in r.text


@pytest.mark.asyncio
async def test_confirmation_shows_zero_value_charge_not_no_charge(
    client: AsyncClient, db, monkeypatch,
):
    """A real zero-value immediate charge (Paddle's `"0"`) must render as a charge
    of zero, never collapse into the "no charge today" wording reserved for
    immediate_amount=None."""
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    _account, owner = await _paid_owner(db, monkeypatch)

    async def prices():
        return [_price("studio", "monthly", "pri_studio_m")]

    async def current_subscription(_subscription_id: str) -> paddle.SubscriptionView:
        return paddle.SubscriptionView(
            status="active", price_id="pri_solo_m", plan_code="solo",
            billing_period="monthly", next_billed_at=None, scheduled_action=None,
            scheduled_at=None, update_payment_method_url=None, cancel_url=None,
        )

    async def preview(subscription_id, price_id, proration):
        return paddle.ChangePreview("0", "2000", "USD", None)

    monkeypatch.setattr(paddle, "list_plan_prices", prices)
    monkeypatch.setattr(paddle, "get_subscription", current_subscription)
    monkeypatch.setattr(paddle, "preview_change", preview)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.get("/ui/billing/confirm?price_id=pri_studio_m")
    assert r.status_code == 200
    assert "USD 0.00" in r.text
    assert "No charge today." not in r.text
