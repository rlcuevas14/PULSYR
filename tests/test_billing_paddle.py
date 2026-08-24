"""Paddle client: configuration contract and response mapping."""

import json

import httpx
import pytest

from app.accounts.plans import PAID_LIMITS
from app.billing import paddle
from app.config import settings


def test_not_configured_without_an_api_key(monkeypatch):
    monkeypatch.setattr(settings, "paddle_api_key", "")
    assert paddle.configured() is False


def test_configured_with_an_api_key(monkeypatch):
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    assert paddle.configured() is True


@pytest.mark.asyncio
async def test_calls_refuse_without_a_key(monkeypatch):
    """One branch for callers: no key means every call raises the same thing."""
    monkeypatch.setattr(settings, "paddle_api_key", "")
    with pytest.raises(paddle.PaddleNotConfigured):
        await paddle.list_plan_prices()


def test_environment_selects_the_api_host(monkeypatch):
    monkeypatch.setattr(settings, "paddle_environment", "sandbox")
    assert paddle._base_url() == "https://sandbox-api.paddle.com"
    monkeypatch.setattr(settings, "paddle_environment", "production")
    assert paddle._base_url() == "https://api.paddle.com"


def _mock_transport(handler):
    """Patch the client factory so no test ever reaches the network."""
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_list_plan_prices_maps_custom_data_to_plan_codes(monkeypatch):
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/prices"
        return httpx.Response(200, json={"data": [
            {"id": "pri_solo_m", "status": "active",
             "unit_price": {"amount": "800", "currency_code": "USD"},
             "billing_cycle": {"interval": "month", "frequency": 1},
             "custom_data": {"plan_code": "solo", "billing_period": "monthly"}},
            {"id": "pri_studio_y", "status": "active",
             "unit_price": {"amount": "20000", "currency_code": "USD"},
             "billing_cycle": {"interval": "year", "frequency": 1},
             "custom_data": {"plan_code": "studio", "billing_period": "yearly"}},
        ]})

    monkeypatch.setattr(paddle, "_transport", _mock_transport(handler))
    prices = await paddle.list_plan_prices()

    assert [(p.plan_code, p.billing_period, p.amount) for p in prices] == [
        ("solo", "monthly", "800"),
        ("studio", "yearly", "20000"),
    ]


@pytest.mark.asyncio
async def test_list_plan_prices_ignores_prices_without_a_known_plan(monkeypatch):
    """A price for something else sold through the same Paddle account must not
    appear as a Pulsyr plan."""
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [
            {"id": "pri_other", "status": "active",
             "unit_price": {"amount": "500", "currency_code": "USD"},
             "billing_cycle": {"interval": "month", "frequency": 1},
             "custom_data": {"plan_code": "enterprise"}},
            {"id": "pri_none", "status": "active",
             "unit_price": {"amount": "500", "currency_code": "USD"},
             "billing_cycle": {"interval": "month", "frequency": 1},
             "custom_data": None},
        ]})

    monkeypatch.setattr(paddle, "_transport", _mock_transport(handler))
    assert await paddle.list_plan_prices() == []
    assert "enterprise" not in PAID_LIMITS


_SUBSCRIPTION = {
    "id": "sub_x",
    "status": "active",
    "next_billed_at": "2026-09-23T12:00:00Z",
    "scheduled_change": {"action": "cancel", "effective_at": "2026-09-23T12:00:00Z"},
    "management_urls": {
        "update_payment_method": "https://pay.paddle.io/update/x",
        "cancel": "https://pay.paddle.io/cancel/x",
    },
    "items": [{"price": {
        "id": "pri_solo_m",
        "custom_data": {"plan_code": "solo", "billing_period": "monthly"},
        "billing_cycle": {"interval": "month", "frequency": 1},
    }}],
}


@pytest.mark.asyncio
async def test_get_subscription_exposes_the_scheduled_change(monkeypatch):
    """A cancellation leaves status active with a scheduled change. The screen
    must be able to say 'Solo until 23 September' rather than 'canceled'."""
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/subscriptions/sub_x"
        return httpx.Response(200, json={"data": _SUBSCRIPTION})

    monkeypatch.setattr(paddle, "_transport", _mock_transport(handler))
    view = await paddle.get_subscription("sub_x")

    assert view.status == "active"
    assert view.plan_code == "solo"
    assert view.billing_period == "monthly"
    assert view.price_id == "pri_solo_m"
    assert view.scheduled_action == "cancel"
    assert view.scheduled_at is not None and view.scheduled_at.year == 2026
    assert view.update_payment_method_url == "https://pay.paddle.io/update/x"


@pytest.mark.asyncio
async def test_get_subscription_without_a_scheduled_change(monkeypatch):
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    payload = {**_SUBSCRIPTION, "scheduled_change": None}

    monkeypatch.setattr(paddle, "_transport", _mock_transport(
        lambda r: httpx.Response(200, json={"data": payload})))
    view = await paddle.get_subscription("sub_x")

    assert view.scheduled_action is None
    assert view.scheduled_at is None


@pytest.mark.asyncio
async def test_paddle_error_is_raised_on_http_failure(monkeypatch):
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    monkeypatch.setattr(paddle, "_transport", _mock_transport(
        lambda r: httpx.Response(500, json={})))
    with pytest.raises(paddle.PaddleError):
        await paddle.get_subscription("sub_x")


@pytest.mark.asyncio
async def test_preview_returns_paddle_figures_not_ours(monkeypatch):
    """The confirmation screen shows what Paddle says, including tax and any
    credit balance we do not track."""
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        return httpx.Response(200, json={"data": {
            "next_billed_at": "2026-09-23T12:00:00Z",
            "immediate_transaction": {"details": {"totals": {
                "grand_total": "1240", "currency_code": "USD"}}},
            "recurring_transaction_details": {"totals": {
                "total": "2000", "currency_code": "USD"}},
        }})

    monkeypatch.setattr(paddle, "_transport", _mock_transport(handler))
    preview = await paddle.preview_change("sub_x", "pri_studio_m", paddle.PRORATION_UPGRADE)

    assert seen == {"path": "/subscriptions/sub_x/preview", "method": "PATCH"}
    assert preview.immediate_amount == "1240"
    assert preview.recurring_amount == "2000"
    assert preview.currency_code == "USD"


@pytest.mark.asyncio
async def test_downgrade_preview_charges_nothing_today(monkeypatch):
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    monkeypatch.setattr(paddle, "_transport", _mock_transport(
        lambda r: httpx.Response(200, json={"data": {
            "immediate_transaction": None,
            "recurring_transaction_details": {"totals": {
                "total": "800", "currency_code": "USD"}},
        }})))

    preview = await paddle.preview_change("sub_x", "pri_solo_m", paddle.PRORATION_DOWNGRADE)
    assert preview.immediate_amount is None
    assert preview.recurring_amount == "800"


@pytest.mark.asyncio
async def test_change_plan_replaces_items_and_prevents_change_on_decline(monkeypatch):
    """items is replace-not-append, and a declined prorated charge must not
    hand out the new plan."""
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["path"] = request.url.path
        sent["method"] = request.method
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {}})

    monkeypatch.setattr(paddle, "_transport", _mock_transport(handler))
    await paddle.change_plan("sub_x", "pri_studio_m", paddle.PRORATION_UPGRADE)

    assert sent["path"] == "/subscriptions/sub_x"
    assert sent["method"] == "PATCH"
    assert sent["body"] == {
        "items": [{"price_id": "pri_studio_m", "quantity": 1}],
        "proration_billing_mode": "prorated_immediately",
        "on_payment_failure": "prevent_change",
    }


@pytest.mark.asyncio
async def test_cancel_is_scheduled_for_the_end_of_the_paid_period(monkeypatch):
    """The Terms promise access continues until the end of the paid period."""
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["path"] = request.url.path
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {}})

    monkeypatch.setattr(paddle, "_transport", _mock_transport(handler))
    await paddle.cancel_subscription("sub_x")

    assert sent["path"] == "/subscriptions/sub_x/cancel"
    assert sent["body"] == {"effective_from": "next_billing_period"}
