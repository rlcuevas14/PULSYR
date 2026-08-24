"""Paddle client: configuration contract and response mapping."""

import pytest

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
