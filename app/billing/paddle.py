"""Outbound Paddle Billing API.

Hand-rolled on httpx for the same reason MCP is: five endpoints do not justify a
package, and a thin surface is easier to mock than an SDK. Every call raises
PaddleNotConfigured when no key is set, so a caller has exactly one branch to
handle rather than a scatter of None checks.
"""

from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

_SANDBOX = "https://sandbox-api.paddle.com"
_PRODUCTION = "https://api.paddle.com"
_TIMEOUT = 15.0
_transport: httpx.AsyncBaseTransport | None = None


class PaddleError(RuntimeError):
    """Paddle answered, and the answer was not usable."""


class PaddleNotConfigured(PaddleError):
    """No API key. The caller should hide its action rather than fail loudly."""


def configured() -> bool:
    return bool(settings.paddle_api_key)


def _base_url() -> str:
    return _PRODUCTION if settings.paddle_environment == "production" else _SANDBOX


async def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    if not configured():
        raise PaddleNotConfigured("PADDLE_API_KEY is not set")
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_transport) as client:
        response = await client.request(
            method,
            f"{_base_url()}{path}",
            headers={"Authorization": f"Bearer {settings.paddle_api_key}"},
            json=payload,
        )
    if response.status_code >= 400:
        raise PaddleError(f"Paddle {method} {path} returned {response.status_code}")
    return response.json().get("data")


@dataclass(frozen=True)
class PlanPrice:
    price_id: str
    plan_code: str
    billing_period: str
    amount: str
    currency_code: str


def _billing_period(price: dict[str, Any]) -> str:
    """Prefer the stamped custom_data, fall back to the cycle Paddle reports."""
    stamped = (price.get("custom_data") or {}).get("billing_period")
    if stamped in ("monthly", "yearly"):
        return str(stamped)
    interval = (price.get("billing_cycle") or {}).get("interval")
    return "yearly" if interval == "year" else "monthly"


async def list_plan_prices() -> list[PlanPrice]:
    """The catalog, filtered to prices that name a plan we actually enforce.

    The plan code lives on the price's custom_data, which is also what the webhook
    trusts. Anything else sold through the same Paddle account is ignored here
    rather than being offered as a Pulsyr plan.
    """
    from app.accounts.plans import PAID_LIMITS

    data = await _request("GET", "/prices") or []
    prices: list[PlanPrice] = []
    for price in data:
        plan_code = (price.get("custom_data") or {}).get("plan_code")
        if plan_code not in PAID_LIMITS:
            continue
        unit = price.get("unit_price") or {}
        prices.append(PlanPrice(
            price_id=str(price["id"]),
            plan_code=str(plan_code),
            billing_period=_billing_period(price),
            amount=str(unit.get("amount", "0")),
            currency_code=str(unit.get("currency_code", "USD")),
        ))
    return prices
