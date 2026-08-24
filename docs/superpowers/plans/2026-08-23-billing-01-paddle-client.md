# Billing 01: Paddle API client

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A typed, mockable, outbound Paddle client that degrades to nothing when no API key is configured.

**Architecture:** One module of plain `httpx` calls returning frozen dataclasses. No SDK dependency: the same reasoning that made MCP hand-rolled applies here, and five endpoints do not justify a package. Every function raises `PaddleNotConfigured` when the key is empty, so callers have one branch to handle.

**Tech Stack:** httpx (already a dependency), Pydantic settings.

**Spec:** `docs/superpowers/specs/2026-08-23-billing-experience-design.md`
**Parent plan:** `docs/superpowers/plans/2026-08-23-billing-experience.md` (its Global Constraints apply to every task here)

---

## File structure

| File | Responsibility |
|---|---|
| `app/billing/__init__.py` | Empty package marker |
| `app/billing/paddle.py` | Every outbound call to Paddle, and the dataclasses that hide its JSON |
| `app/config.py` | Three new settings |
| `.env.example` | Document the three settings |
| `tests/test_billing_paddle.py` | Contract tests against mocked HTTP |

`app/accounts/plans.py` is deliberately untouched: it stays free of outbound HTTP so it remains the auditable entitlement authority.

---

### Task 1: Settings and the not-configured contract

**Files:**
- Create: `app/billing/__init__.py`, `app/billing/paddle.py`
- Modify: `app/config.py:70-76` (beside `paddle_webhook_secret`), `.env.example`
- Test: `tests/test_billing_paddle.py`

**Interfaces:**
- Consumes: `app.config.settings`
- Produces: `PaddleError`, `PaddleNotConfigured`, `configured()`, `_base_url()`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_billing_paddle.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_billing_paddle.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.billing'`

- [ ] **Step 3: Add the settings**

In `app/config.py`, directly below `paddle_webhook_secret`:

```python
    # Server-side Paddle key. Empty on a self-hosted install: the billing screen
    # then renders plan and usage and hides every action.
    paddle_api_key: str = ""
    # Public by design: this one is embedded in the page for Paddle.js.
    paddle_client_token: str = ""
    # Selects both the API host and the Paddle.js environment. Sandbox keys are
    # prefixed pdl_sdbx_ and cannot reach live even if this is set wrong.
    paddle_environment: str = "sandbox"
```

In `.env.example`, below the `PADDLE_WEBHOOK_SECRET` block:

```bash
# PADDLE_API_KEY=
# PADDLE_CLIENT_TOKEN=
# PADDLE_ENVIRONMENT=sandbox
```

- [ ] **Step 4: Write the module skeleton**

Create `app/billing/__init__.py` empty. Create `app/billing/paddle.py`:

```python
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
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.request(
            method,
            f"{_base_url()}{path}",
            headers={"Authorization": f"Bearer {settings.paddle_api_key}"},
            json=payload,
        )
    if response.status_code >= 400:
        raise PaddleError(f"Paddle {method} {path} returned {response.status_code}")
    return response.json().get("data")


async def list_plan_prices() -> list["PlanPrice"]:
    raise NotImplementedError
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_billing_paddle.py -q`
Expected: 4 passed. `test_calls_refuse_without_a_key` passes because `list_plan_prices` reaches `_request`... it does not yet. Make `list_plan_prices` call `_request("GET", "/prices")` before running, so the guard is what raises.

- [ ] **Step 6: Commit**

```bash
git add app/billing/__init__.py app/billing/paddle.py app/config.py .env.example tests/test_billing_paddle.py
git commit -m "feat(billing): add the Paddle client configuration contract"
```

---

### Task 2: Read the catalog

**Files:**
- Modify: `app/billing/paddle.py`
- Test: `tests/test_billing_paddle.py`

**Interfaces:**
- Produces: `PlanPrice`, `list_plan_prices() -> list[PlanPrice]`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_billing_paddle.py
import httpx

from app.accounts.plans import PAID_LIMITS


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_billing_paddle.py -q`
Expected: FAIL with `AttributeError: module 'app.billing.paddle' has no attribute '_transport'`

- [ ] **Step 3: Add the seam and the implementation**

In `app/billing/paddle.py`, add a module-level transport that tests replace, and use it in `_request`:

```python
_transport: httpx.BaseTransport | None = None
```

Change the client construction inside `_request` to:

```python
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_transport) as client:
```

Then add:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_billing_paddle.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/billing/paddle.py tests/test_billing_paddle.py
git commit -m "feat(billing): read the plan catalog from Paddle"
```

---

### Task 3: Read one subscription

**Files:**
- Modify: `app/billing/paddle.py`
- Test: `tests/test_billing_paddle.py`

**Interfaces:**
- Produces: `SubscriptionView`, `get_subscription(subscription_id: str) -> SubscriptionView`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_billing_paddle.py

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_billing_paddle.py -q`
Expected: FAIL with `AttributeError: module 'app.billing.paddle' has no attribute 'get_subscription'`

- [ ] **Step 3: Implement**

```python
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
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_billing_paddle.py -q`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add app/billing/paddle.py tests/test_billing_paddle.py
git commit -m "feat(billing): read subscription detail live from Paddle"
```

---

### Task 4: Preview, change and cancel

**Files:**
- Modify: `app/billing/paddle.py`
- Test: `tests/test_billing_paddle.py`

**Interfaces:**
- Produces: `ChangePreview`, `preview_change`, `change_plan`, `cancel_subscription`, `PRORATION_UPGRADE`, `PRORATION_DOWNGRADE`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_billing_paddle.py

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
```

Add `import json` to the test file's imports.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_billing_paddle.py -q`
Expected: FAIL with `AttributeError: module 'app.billing.paddle' has no attribute 'PRORATION_UPGRADE'`

- [ ] **Step 3: Implement**

```python
# Upgrade: the customer gets capacity now and pays the difference now. Downgrade
# and term shortening: credit the difference at renewal instead of refunding
# mid-period. Paddle applies the item change immediately in every mode; only the
# billing timing is selectable.
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
    immediate = (data.get("immediate_transaction") or {}).get("details", {}).get("totals", {})
    recurring = (data.get("recurring_transaction_details") or {}).get("totals", {})
    return ChangePreview(
        immediate_amount=immediate.get("grand_total"),
        recurring_amount=str(recurring.get("total", "0")),
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_billing_paddle.py -q`
Expected: 13 passed

- [ ] **Step 5: Run the full gates**

```bash
ruff check app/ tests/
python -m mypy app/
```
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add app/billing/paddle.py tests/test_billing_paddle.py
git commit -m "feat(billing): preview, change and cancel subscriptions"
```

---

### Task 5: Verify the REST paths against the real sandbox

The four endpoint paths above are written from the API reference, not from a
live call. A wrong path fails at the worst moment, so prove them once now while
the catalog is the only thing in the account.

- [ ] **Step 1: Confirm the catalog call works end to end**

With `PADDLE_API_KEY` and `PADDLE_ENVIRONMENT=sandbox` exported, in a Python REPL:

```python
import asyncio
from app.billing import paddle
print(asyncio.run(paddle.list_plan_prices()))
```

Expected: four `PlanPrice` entries, two `solo` and two `studio`.

- [ ] **Step 2: Record the result**

If any path is wrong, fix it in `app/billing/paddle.py`, adjust the asserted path
in the matching test, and re-run `python -m pytest tests/test_billing_paddle.py -q`.
The subscription, preview, change and cancel paths cannot be exercised until a
sandbox subscription exists; plan 3 creates the first one and is where those
three get their live confirmation.

- [ ] **Step 3: Commit any correction**

```bash
git add app/billing/paddle.py tests/test_billing_paddle.py
git commit -m "fix(billing): correct a Paddle endpoint path against the sandbox"
```
