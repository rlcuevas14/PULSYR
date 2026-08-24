"""Paddle client: configuration contract and response mapping."""

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
