"""Outbound Paddle Billing API.

Hand-rolled on httpx for the same reason MCP is: five endpoints do not justify a
package, and a thin surface is easier to mock than an SDK. Every call raises
PaddleNotConfigured when no key is set, so a caller has exactly one branch to
handle rather than a scatter of None checks.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
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
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_transport) as client:
            response = await client.request(
                method,
                f"{_base_url()}{path}",
                headers={"Authorization": f"Bearer {settings.paddle_api_key}"},
                json=payload,
            )
    except httpx.RequestError as exc:
        # A timeout or a refused connection is a provider outage exactly like a 5xx,
        # and every caller already knows how to answer one. Left unwrapped it escapes
        # as a 500, so the customer sees a crash on the screen where they were about
        # to pay, and the likeliest real outage is this shape rather than a 5xx.
        # The exception type is named, never the URL: the URL carries the key.
        raise PaddleError(f"Paddle {method} {path} unreachable: {type(exc).__name__}") from exc
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
    subscription with no items at all does not reach that decision: is_downgrade
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


# One mode for every plan change, in both directions. Paddle works out the sign:
# an upgrade charges the prorated difference today, a downgrade charges nothing
# and credits the difference to the customer's Paddle balance, which then offsets
# their future invoices. Verified against the sandbox: a downgrade from an annual
# plan previewed grand_total 0 with credit_to_balance 19198 and result.action
# "credit", so nothing is refunded to a card without being asked for.
#
# The obvious-looking prorated_next_billing_period is NOT usable for a downgrade.
# Paddle rejects it with "the new items are not valid for updating this
# subscription" for every downgrade shape out of an annual subscription: lower
# tier, shorter term, and both at once. The mode means "bill the adjustment on the
# next invoice", and an annual plan's next invoice is a year away while the
# adjustment is a credit, so there is nothing to attach it to. Using it would have
# left every annual customer unable to change plan at all.
PRORATION = "prorated_immediately"


@dataclass(frozen=True)
class ChangePreview:
    immediate_amount: str | None
    recurring_amount: str
    currency_code: str
    next_billed_at: datetime | None


def _change_body(price_id: str) -> dict[str, Any]:
    # items is replace, not append: send the complete list the subscription
    # should end up with. on_payment_failure keeps a declined card from handing
    # out a plan nobody paid for.
    return {
        "items": [{"price_id": price_id, "quantity": 1}],
        "proration_billing_mode": PRORATION,
        "on_payment_failure": "prevent_change",
    }


async def preview_change(subscription_id: str, price_id: str) -> ChangePreview:
    data = await _request(
        "PATCH", f"/subscriptions/{subscription_id}/preview",
        _change_body(price_id),
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


async def change_plan(subscription_id: str, price_id: str) -> None:
    """Apply the change. Deliberately returns nothing: the resulting plan state
    arrives through the webhook, which is the only writer of the local mirror."""
    await _request("PATCH", f"/subscriptions/{subscription_id}", _change_body(price_id))


async def cancel_subscription(subscription_id: str) -> None:
    await _request(
        "POST", f"/subscriptions/{subscription_id}/cancel",
        {"effective_from": "next_billing_period"},
    )


async def resume_plan(subscription_id: str) -> None:
    """Undo a scheduled cancellation.

    Paddle stores a pending cancel as a `scheduled_change` on the subscription,
    so removing it is a null on that field rather than a delete of anything. The
    subscription never left `active`, which is why this is a resume and not a
    re-subscribe: no new checkout, no new card, no gap in access.
    """
    await _request("PATCH", f"/subscriptions/{subscription_id}", {"scheduled_change": None})


@dataclass(frozen=True)
class Movement:
    """One line of billing history, from either side of the money.

    Paddle splits history across two resources and we show one list, because
    "what happened to my money" is one question. A charge is a transaction; a
    credit, refund or chargeback is an adjustment. Merging them is the whole
    point: a downgrade writes a zero-value transaction AND a credit adjustment,
    and reading only transactions would show the customer a $0.00 line where
    their money actually moved.
    """
    kind: str  # "charge" | "credit": decides the sign the template shows
    label: str  # already normalised to a key that exists in the catalogs
    occurred_at: datetime | None
    amount: str
    currency_code: str
    state: str  # "settled" | "pending" | "voided"
    reference: str
    transaction_id: str | None  # only a charge has an invoice to download


# Normalised here rather than in the template so an origin Paddle adds later
# falls back to a key that exists, instead of rendering "billing.movement.foo"
# on the screen through t()'s key-as-fallback.
_CHARGE_LABELS = {
    "web": "start",
    "subscription_recurring": "renewal",
    "subscription_update": "change",
    "subscription_charge": "change",
}
_CREDIT_LABELS = {"credit": "credit", "refund": "refund", "chargeback": "chargeback"}
_SETTLED = {"completed", "paid", "billed", "approved"}
_VOIDED = {"canceled", "cancelled", "rejected", "reversed"}


def _state(status: str) -> str:
    if status in _SETTLED:
        return "settled"
    return "voided" if status in _VOIDED else "pending"


async def list_movements(subscription_id: str) -> list[Movement]:
    """The subscription's billing history, newest first.

    Both reads are filtered by subscription id at Paddle rather than here, so a
    caller can only ever see the history of the subscription it passed in.
    """
    charges = await _request(
        "GET", f"/transactions?subscription_id={subscription_id}&per_page=50",
    ) or []
    credits = await _request(
        "GET", f"/adjustments?subscription_id={subscription_id}&per_page=50",
    ) or []

    movements: list[Movement] = []
    for txn in charges:
        totals = ((txn.get("details") or {}).get("totals") or {})
        movements.append(Movement(
            kind="charge",
            label=_CHARGE_LABELS.get(str(txn.get("origin", "")), "charge"),
            occurred_at=_dt(txn.get("billed_at") or txn.get("created_at")),
            amount=str(totals.get("grand_total", "0")),
            currency_code=str(txn.get("currency_code", "USD")),
            state=_state(str(txn.get("status", ""))),
            reference=str(txn.get("invoice_number") or ""),
            transaction_id=str(txn["id"]),
        ))
    for adj in credits:
        movements.append(Movement(
            kind="credit",
            label=_CREDIT_LABELS.get(str(adj.get("action", "")), "credit"),
            occurred_at=_dt(adj.get("created_at")),
            amount=str((adj.get("totals") or {}).get("total", "0")),
            currency_code=str(adj.get("currency_code", "USD")),
            state=_state(str(adj.get("status", ""))),
            reference=str(adj.get("credit_note_number") or ""),
            transaction_id=None,
        ))

    # datetime.min is only a sort key for a row Paddle sent without any date at
    # all; it sinks to the bottom rather than crashing the comparison. It is
    # timezone-aware because every real value here is, and Python refuses to
    # order aware against naive.
    floor = datetime.min.replace(tzinfo=UTC)
    movements.sort(key=lambda m: m.occurred_at or floor, reverse=True)
    return movements


async def invoice_url(transaction_id: str, subscription_id: str) -> str | None:
    """A signed link to one invoice PDF, or None if it is not this
    subscription's.

    The transaction id arrives in a URL, so it is attacker-controlled, and the
    only authority on who owns it is Paddle. Checking here rather than trusting
    the id keeps one owner's invoice out of another owner's browser, and the
    link is minted on demand instead of on every render of the history: it is
    one extra call when someone clicks, rather than N calls for a list nobody
    may click at all.
    """
    txn = await _request("GET", f"/transactions/{transaction_id}") or {}
    if str(txn.get("subscription_id") or "") != subscription_id:
        return None
    data = await _request("GET", f"/transactions/{transaction_id}/invoice") or {}
    return str(data["url"]) if data.get("url") else None
