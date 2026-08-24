"""Outbound Paddle Billing API.

Hand-rolled on httpx for the same reason MCP is: five endpoints do not justify a
package, and a thin surface is easier to mock than an SDK. Every call raises
PaddleNotConfigured when no key is set, so a caller has exactly one branch to
handle rather than a scatter of None checks.
"""

from dataclasses import dataclass
from datetime import datetime
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
    """Prefer the stamped custom_data, fall back to the cycle Paddle reports.

    The final "monthly" is only ever a tiebreaker between two prices of the same
    tier, so guessing wrong there costs a proration mode, never a tier. A
    subscription with no items at all does not reach that decision: proration_for
    refuses an unrecognised plan code first and credits at renewal instead.
    """
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


@dataclass(frozen=True)
class SubscriptionView:
    status: str
    price_id: str
    plan_code: str
    billing_period: str
    next_billed_at: datetime | None
    scheduled_action: str | None
    scheduled_at: datetime | None
    update_payment_method_url: str | None
    cancel_url: str | None


def _dt(value: Any) -> datetime | None:
    """Deliberate twin of `_parse_dt` in app/webhooks/service.py.

    They stay separate because they belong to different layers, an outbound API
    client and an inbound webhook parser, and neither should have to import the
    other to read a timestamp. Both swallow the same two failures: a string that
    is not a date (ValueError) and a value that is not a string at all
    (TypeError). Keep them in step.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


async def get_subscription(subscription_id: str) -> SubscriptionView:
    """Billing detail read live rather than mirrored.

    Everything here changes without a webhook we subscribe to, or goes stale the
    moment it is copied: the next billing date, a pending cancellation, the URL
    Paddle mints for updating a card. The local mirror keeps only what the
    entitlement guards read on every request.
    """
    data = await _request("GET", f"/subscriptions/{subscription_id}") or {}
    items = data.get("items") or []
    price = (items[0].get("price") if items else {}) or {}
    custom = price.get("custom_data") or {}
    scheduled = data.get("scheduled_change") or {}
    urls = data.get("management_urls") or {}
    return SubscriptionView(
        status=str(data.get("status", "")),
        price_id=str(price.get("id", "")),
        plan_code=str(custom.get("plan_code", "")),
        billing_period=_billing_period(price),
        next_billed_at=_dt(data.get("next_billed_at")),
        scheduled_action=str(scheduled["action"]) if scheduled.get("action") else None,
        scheduled_at=_dt(scheduled.get("effective_at")),
        update_payment_method_url=urls.get("update_payment_method"),
        cancel_url=urls.get("cancel"),
    )


PRORATION_UPGRADE = "prorated_immediately"
PRORATION_DOWNGRADE = "prorated_next_billing_period"


@dataclass(frozen=True)
class ChangePreview:
    immediate_amount: str | None
    recurring_amount: str
    currency_code: str
    next_billed_at: datetime | None


def _change_body(price_id: str, proration: str) -> dict[str, Any]:
    # items is replace, not append: send the complete list the subscription
    # should end up with. on_payment_failure keeps a declined card from handing
    # out a plan nobody paid for.
    return {
        "items": [{"price_id": price_id, "quantity": 1}],
        "proration_billing_mode": proration,
        "on_payment_failure": "prevent_change",
    }


async def preview_change(subscription_id: str, price_id: str, proration: str) -> ChangePreview:
    data = await _request(
        "PATCH", f"/subscriptions/{subscription_id}/preview",
        _change_body(price_id, proration),
    ) or {}
    # `or {}` at every level: Paddle sends an explicit null for a block that does
    # not apply, and a null is not a dict to call .get on.
    immediate = (
        ((data.get("immediate_transaction") or {}).get("details") or {}).get("totals") or {}
    )
    recurring = (data.get("recurring_transaction_details") or {}).get("totals") or {}

    # grand_total on the immediate transaction, total on the recurring one, and
    # the difference is not cosmetic: the immediate figure is the whole amount
    # the card is charged today, tax and credit balance included, while the
    # recurring block quotes the subscription's own total for the next period.
    recurring_total = recurring.get("total")
    if recurring_total is None:
        # A confirmation screen that cannot state the recurring price must not be
        # rendered at all. Defaulting to "0" would quote a free plan for a paid
        # one, next to an immediate amount that correctly stays None.
        raise PaddleError("Paddle preview carried no recurring total")

    return ChangePreview(
        immediate_amount=immediate.get("grand_total"),
        recurring_amount=str(recurring_total),
        currency_code=str(recurring.get("currency_code") or immediate.get("currency_code") or "USD"),
        next_billed_at=_dt(data.get("next_billed_at")),
    )


async def change_plan(subscription_id: str, price_id: str, proration: str) -> None:
    """Apply the change. Deliberately returns nothing: the resulting plan state
    arrives through the webhook, which is the only writer of the local mirror."""
    await _request("PATCH", f"/subscriptions/{subscription_id}", _change_body(price_id, proration))


async def cancel_subscription(subscription_id: str) -> None:
    await _request(
        "POST", f"/subscriptions/{subscription_id}/cancel",
        {"effective_from": "next_billing_period"},
    )
