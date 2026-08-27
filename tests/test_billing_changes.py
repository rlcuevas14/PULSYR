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
    assert service.is_downgrade(current, target) is False


def test_tier_downgrade_is_recognised():
    """Dropping a tier is a downgrade, whatever it ends up billing."""
    assert service.is_downgrade(_price("studio", "monthly"), _price("solo", "monthly")
    ) is True


def test_monthly_to_yearly_charges_now():
    """A year is a much larger payment; charging now is the honest moment."""
    assert service.is_downgrade(_price("solo", "monthly"), _price("solo", "yearly")
    ) is False


def test_yearly_to_monthly_is_a_downgrade():
    """A shorter term inside one tier is the smaller commitment."""
    assert service.is_downgrade(_price("solo", "yearly"), _price("solo", "monthly")
    ) is True


def test_tier_change_wins_over_term_change():
    """Studio yearly to Solo monthly is a downgrade even though the term also
    shortens: the smaller of the two must not be charged immediately."""
    assert service.is_downgrade(_price("studio", "yearly"), _price("solo", "monthly")
    ) is True


def test_tier_upgrade_overrides_term_downgrade():
    """Solo yearly to Studio monthly is an upgrade: tier wins even when the
    term shortens. This proves tier is checked before term in the decision."""
    assert service.is_downgrade(_price("solo", "yearly"), _price("studio", "monthly")
    ) is False


def test_unknown_plan_code_warns():
    """When Paddle omits items, plan_code is empty. Warn rather than stay
    silent: an unnecessary caution costs nothing, a missing one costs capacity."""
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
    assert service.is_downgrade(current, target) is True


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
        # is_downgrade compares the target against, independent of the local
        # mirror's plan_code.
        return paddle.SubscriptionView(
            status="active", price_id="pri_solo_m", plan_code="solo",
            billing_period="monthly", next_billed_at=None, scheduled_action=None,
            scheduled_at=None, update_payment_method_url=None, cancel_url=None,
        )

    async def preview(subscription_id, price_id):
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
async def test_confirmation_reports_a_paddle_outage_as_a_provider_error(
    client: AsyncClient, db, monkeypatch,
):
    """The confirmation route used to wrap nothing, so an outage anywhere in its
    three Paddle calls surfaced as a 500. Every billing action answers a Paddle
    outage the same way, and 502 is the one that says whose fault it is."""
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    _account, owner = await _paid_owner(db, monkeypatch)

    async def prices():
        return [_price("studio", "monthly", "pri_studio_m")]

    async def get_sub(_subscription_id):
        return paddle.SubscriptionView(
            "active", "pri_solo_m", "solo", "monthly", None, None, None, None, None)

    async def boom(subscription_id, price_id):
        raise paddle.PaddleError("down")

    monkeypatch.setattr(paddle, "list_plan_prices", prices)
    monkeypatch.setattr(paddle, "get_subscription", get_sub)
    monkeypatch.setattr(paddle, "preview_change", boom)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.get("/ui/billing/confirm?price_id=pri_studio_m")
    assert r.status_code == 502


@pytest.mark.asyncio
async def test_confirmation_shows_downgrade_warning_and_no_charge_today(
    client: AsyncClient, db, monkeypatch,
):
    """A downgrade must show the capacity-drops-now warning and must not claim a
    charge today: Paddle reports no immediate transaction for a downgrade. The
    proration mode is not hardcoded here; it is left for the route to derive from
    is_downgrade."""
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

    async def preview(subscription_id, price_id):
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

    async def preview(subscription_id, price_id):
        return paddle.ChangePreview("0", "2000", "USD", None)

    monkeypatch.setattr(paddle, "list_plan_prices", prices)
    monkeypatch.setattr(paddle, "get_subscription", current_subscription)
    monkeypatch.setattr(paddle, "preview_change", preview)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.get("/ui/billing/confirm?price_id=pri_studio_m")
    assert r.status_code == 200
    assert "USD 0.00" in r.text
    assert "No charge today." not in r.text


@pytest.mark.asyncio
async def test_an_unparseable_amount_falls_through_instead_of_erroring(
    client: AsyncClient, db, monkeypatch,
):
    """_money parses Paddle's lowest-denomination string. Anything that is not an
    integer used to raise into a 500 on a page about money; the template already
    has a branch for "no figure to show" and falling into it is the honest
    answer."""
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    _account, owner = await _paid_owner(db, monkeypatch)

    async def prices():
        return [_price("studio", "monthly", "pri_studio_m")]

    async def current_subscription(_subscription_id: str) -> paddle.SubscriptionView:
        return paddle.SubscriptionView(
            "active", "pri_solo_m", "solo", "monthly", None, None, None, None, None)

    async def preview(subscription_id, price_id):
        return paddle.ChangePreview("12.40", "2000", "USD", None)

    monkeypatch.setattr(paddle, "list_plan_prices", prices)
    monkeypatch.setattr(paddle, "get_subscription", current_subscription)
    monkeypatch.setattr(paddle, "preview_change", preview)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.get("/ui/billing/confirm?price_id=pri_studio_m")
    assert r.status_code == 200
    assert "No charge today." in r.text


@pytest.mark.asyncio
async def test_an_unparseable_recurring_amount_is_a_provider_error(
    client: AsyncClient, db, monkeypatch,
):
    """The recurring price is the one figure a confirmation screen may never
    omit: a customer confirming a charge must never see the literal string
    "None" where a price belongs. Unlike the immediate amount, an unparseable
    recurring_amount must fail the whole screen as a provider error rather than
    fall through to a blank field."""
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    _account, owner = await _paid_owner(db, monkeypatch)

    async def prices():
        return [_price("studio", "monthly", "pri_studio_m")]

    async def current_subscription(_subscription_id: str) -> paddle.SubscriptionView:
        return paddle.SubscriptionView(
            "active", "pri_solo_m", "solo", "monthly", None, None, None, None, None)

    async def preview(subscription_id, price_id):
        return paddle.ChangePreview("1240", "not-a-number", "USD", None)

    monkeypatch.setattr(paddle, "list_plan_prices", prices)
    monkeypatch.setattr(paddle, "get_subscription", current_subscription)
    monkeypatch.setattr(paddle, "preview_change", preview)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.get("/ui/billing/confirm?price_id=pri_studio_m")
    assert r.status_code == 502
    assert "None" not in r.text


@pytest.mark.asyncio
async def test_change_calls_paddle_and_writes_nothing_locally(client: AsyncClient, db, monkeypatch):
    """The webhook is the only writer. A route that also wrote would create a
    second source of truth that disagrees the moment a payment declines."""
    from app.accounts.plans import subscription_for

    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    account, owner = await _paid_owner(db, monkeypatch)
    # Captured now, before db.expire_all() below expires it: reading an expired
    # attribute as a bare argument (rather than through an awaited SQLAlchemy
    # call) crashes with MissingGreenlet, same as the account_id parameter
    # pattern already used in tests/test_paddle_webhook.py's _subscription().
    account_id = account.id
    called = {}

    async def prices():
        return [_price("studio", "monthly", "pri_studio_m")]

    async def get_sub(subscription_id):
        return paddle.SubscriptionView(
            "active", "pri_solo_m", "solo", "monthly", None, None, None, None, None)

    async def change(subscription_id, price_id):
        called["args"] = (subscription_id, price_id)

    monkeypatch.setattr(paddle, "list_plan_prices", prices)
    monkeypatch.setattr(paddle, "get_subscription", get_sub)
    monkeypatch.setattr(paddle, "change_plan", change)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.post("/ui/billing/change", data={"price_id": "pri_studio_m"})
    assert r.status_code in (200, 204)
    assert called["args"][1:] == ("pri_studio_m",)

    db.expire_all()
    row = await subscription_for(db, account_id)
    assert row.plan_code == "solo"  # unchanged until the webhook arrives


@pytest.mark.asyncio
async def test_a_declined_change_is_reported_not_swallowed(client: AsyncClient, db, monkeypatch):
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    _account, owner = await _paid_owner(db, monkeypatch)

    async def prices():
        return [_price("studio", "monthly", "pri_studio_m")]

    async def get_sub(subscription_id):
        return paddle.SubscriptionView(
            "active", "pri_solo_m", "solo", "monthly", None, None, None, None, None)

    async def boom(subscription_id, price_id):
        raise paddle.PaddleError("card declined")

    monkeypatch.setattr(paddle, "list_plan_prices", prices)
    monkeypatch.setattr(paddle, "get_subscription", get_sub)
    monkeypatch.setattr(paddle, "change_plan", boom)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.post("/ui/billing/change", data={"price_id": "pri_studio_m"})
    assert r.status_code >= 400


@pytest.mark.asyncio
async def test_change_reports_an_outage_in_the_reads_it_makes_first(
    client: AsyncClient, db, monkeypatch,
):
    """The change route resolves the target against the catalog before asking
    Paddle to do anything. That read sat outside the try block once, so an outage
    in it was an unhandled error rather than the 502 the design asks for. The
    read it fails on is the catalog: the route no longer reads the subscription,
    because there is one proration mode and nothing left to derive."""
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    _account, owner = await _paid_owner(db, monkeypatch)

    async def boom():
        raise paddle.PaddleError("down")

    async def change(subscription_id, price_id):
        raise AssertionError("a failed read must never reach the write")

    monkeypatch.setattr(paddle, "list_plan_prices", boom)
    monkeypatch.setattr(paddle, "change_plan", change)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.post("/ui/billing/change", data={"price_id": "pri_studio_m"})
    assert r.status_code == 502


@pytest.mark.asyncio
async def test_cancel_schedules_and_keeps_access(client: AsyncClient, db, monkeypatch):
    """Cancelling is not losing access today. The Terms promise the paid period."""
    from app.accounts.plans import subscription_for

    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    account, owner = await _paid_owner(db, monkeypatch)
    # Captured now, before db.expire_all() below expires it: reading an expired
    # attribute as a bare argument (rather than through an awaited SQLAlchemy
    # call) crashes with MissingGreenlet, same as the account_id parameter
    # pattern already used above in test_change_calls_paddle_and_writes_nothing_locally.
    account_id = account.id
    called = {}

    async def cancel(subscription_id):
        called["id"] = subscription_id

    monkeypatch.setattr(paddle, "cancel_subscription", cancel)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.post("/ui/billing/cancel")
    assert r.status_code in (200, 204)
    assert called["id"].startswith("sub_")

    db.expire_all()
    row = await subscription_for(db, account_id)
    assert (row.plan_code, row.status) == ("solo", "active")


@pytest.mark.asyncio
async def test_a_network_outage_gives_502_not_500(client: AsyncClient, db, monkeypatch):
    """End to end for the whole chain: the transport fails, paddle._request turns
    that into a PaddleError, and the route answers the same 502 it gives for a
    provider 5xx. Before this the customer got a crash page instead."""
    import httpx

    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    _account, owner = await _paid_owner(db, monkeypatch)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection timed out", request=request)

    monkeypatch.setattr(paddle, "_transport", httpx.MockTransport(refuse))
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.post("/ui/billing/cancel")
    assert r.status_code == 502


@pytest.mark.asyncio
async def test_member_cannot_cancel(client: AsyncClient, db, monkeypatch):
    from app.accounts.members import create_member

    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    account, _owner = await _paid_owner(db, monkeypatch)
    member = await create_member(
        db, account_id=account.id, email=f"m-{uuid.uuid4().hex[:6]}@test.cl",
        name="Member", password="secret-password",
    )
    await db.commit()

    async def cancel(subscription_id):
        raise AssertionError("a member must never reach the Paddle call")

    monkeypatch.setattr(paddle, "cancel_subscription", cancel)
    await client.post("/login", data={"email": member.email, "password": "secret-password"})

    r = await client.post("/ui/billing/cancel")
    assert r.status_code in (403, 404)
