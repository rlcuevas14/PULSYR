# Billing 04: plan changes and cancellation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A paying owner can move between Solo and Studio, switch between monthly and yearly, and cancel, seeing what it costs before confirming.

**Architecture:** Two steps, always. A confirmation screen renders Paddle's own preview figures, and only a second request applies the change. The route never writes a plan: it calls Paddle and returns a flash saying the change was submitted, because the webhook is what makes it true a moment later.

**Tech Stack:** FastAPI, HTMX 2, Jinja2.

**Spec:** `docs/superpowers/specs/2026-08-23-billing-experience-design.md`
**Parent plan:** `docs/superpowers/plans/2026-08-23-billing-experience.md` (its Global Constraints apply to every task here)
**Depends on:** plans 01, 02 and 03

---

## File structure

| File | Responsibility |
|---|---|
| `app/billing/router.py` | `GET /ui/billing/confirm`, `POST /ui/billing/change`, `POST /ui/billing/cancel` |
| `app/billing/service.py` | Which proration a given move needs, and the ownership check |
| `app/templates/billing_confirm.html` | The confirmation step |
| `app/templates/billing.html` | Change and cancel buttons on the plan cards |
| `app/i18n/locales/{en,es,fr}.json` | Copy |
| `tests/test_billing_changes.py` | Proration choice, ownership, the lag, cancellation |

---

### Task 1: Decide the proration, and prove it

The single most consequential line in this plan is which proration mode a given
move uses. It decides whether a customer is charged, credited or refunded, so it
gets its own unit under test before any route exists.

**Files:**
- Create: `app/billing/service.py`
- Test: `tests/test_billing_changes.py`

**Interfaces:**
- Consumes: `paddle.PRORATION_UPGRADE`, `paddle.PRORATION_DOWNGRADE`, `paddle.PlanPrice`
- Produces: `PLAN_RANK`, `proration_for(current: PlanPrice | SubscriptionView, target: PlanPrice) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_billing_changes.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_billing_changes.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.billing.service'`

- [ ] **Step 3: Implement**

Create `app/billing/service.py`:

```python
"""Rules that decide what a plan change costs, kept apart from the HTTP layer
so they can be read and tested as rules."""

from typing import Protocol

from app.billing import paddle

# Ordered by capacity, not by price. A move up this list is an upgrade.
PLAN_RANK: dict[str, int] = {"free": 0, "solo": 1, "studio": 2}
_TERM_RANK: dict[str, int] = {"monthly": 0, "yearly": 1}


class _Priced(Protocol):
    plan_code: str
    billing_period: str


def proration_for(current: _Priced, target: paddle.PlanPrice) -> str:
    """Charge immediately only when the customer is getting more.

    Tier decides first: dropping a tier is a downgrade even when the term also
    changes, because charging immediately for a smaller plan would be indefensible.
    Term is the tiebreaker within the same tier, where moving to a year is the
    larger payment and belongs today.
    """
    current_tier = PLAN_RANK.get(current.plan_code, 0)
    target_tier = PLAN_RANK.get(target.plan_code, 0)
    if target_tier != current_tier:
        return paddle.PRORATION_UPGRADE if target_tier > current_tier else paddle.PRORATION_DOWNGRADE

    current_term = _TERM_RANK.get(current.billing_period, 0)
    target_term = _TERM_RANK.get(target.billing_period, 0)
    return paddle.PRORATION_UPGRADE if target_term > current_term else paddle.PRORATION_DOWNGRADE
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_billing_changes.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/billing/service.py tests/test_billing_changes.py
git commit -m "feat(billing): decide proration by tier first, then term"
```

---

### Task 2: The confirmation step

**Files:**
- Modify: `app/billing/router.py`
- Create: `app/templates/billing_confirm.html`
- Test: `tests/test_billing_changes.py`

**Interfaces:**
- Produces: `GET /ui/billing/confirm?price_id=...`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_billing_changes.py

async def _paid_owner(db, monkeypatch, plan_code="solo"):
    """An account whose mirror already carries a Paddle subscription."""
    from app.accounts.plans import apply_paddle_subscription
    from datetime import datetime, timezone

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

    async def preview(subscription_id, price_id, proration):
        assert proration == paddle.PRORATION_UPGRADE
        return paddle.ChangePreview("1240", "2000", "USD", None)

    monkeypatch.setattr(paddle, "list_plan_prices", prices)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_billing_changes.py -q`
Expected: FAIL with 404

- [ ] **Step 3: Implement**

Add to `app/billing/router.py`:

```python
def _money(amount: str | None, currency: str) -> str | None:
    """Paddle sends the lowest denomination as a string. Two decimals covers
    every currency the catalog uses; a zero-decimal currency such as CLP or JPY
    would need its own case here."""
    if amount is None:
        return None
    return f"{currency} {int(amount) / 100:.2f}"


async def _resolve_target(price_id: str) -> paddle.PlanPrice:
    for price in await paddle.list_plan_prices():
        if price.price_id == price_id:
            return price
    raise HTTPException(status_code=400, detail="unknown price")


@router.get("/ui/billing/confirm", response_class=HTMLResponse)
async def billing_confirm(
    request: Request,
    price_id: str = Query(...),
    user: User = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    subscription = await plans.subscription_for(db, user.account_id)
    if subscription is None or not subscription.paddle_subscription_id:
        raise HTTPException(status_code=400, detail="no subscription to change")

    target = await _resolve_target(price_id)
    current = await paddle.get_subscription(subscription.paddle_subscription_id)
    proration = billing_service.proration_for(current, target)
    preview = await paddle.preview_change(
        subscription.paddle_subscription_id, target.price_id, proration
    )

    return templates.TemplateResponse(request, "billing_confirm.html", {
        "target": target,
        "immediate": _money(preview.immediate_amount, preview.currency_code),
        "recurring": _money(preview.recurring_amount, preview.currency_code),
        "next_billed_at": preview.next_billed_at,
        "is_downgrade": proration == paddle.PRORATION_DOWNGRADE,
    })
```

Import `from app.billing import service as billing_service` at the top.

- [ ] **Step 4: Write the template**

Create `app/templates/billing_confirm.html`:

```jinja
{% extends "base.html" %}
{% block content %}
<h1 class="text-2xl font-semibold mb-4">{{ t("billing.confirm_title", plan=t("plan." ~ target.plan_code)) }}</h1>

{% if immediate %}
<p>{{ t("billing.confirm_charge_now", amount=immediate) }}</p>
{% else %}
<p>{{ t("billing.confirm_no_charge_now") }}</p>
{% endif %}
<p>{{ t("billing.confirm_recurring", amount=recurring, term=t("billing.term." ~ target.billing_period)) }}</p>

{% if is_downgrade %}
<div class="my-4 bg-warning/10 border border-warning/30 text-warning-strong rounded-xl px-4 py-3 text-sm">
  <p>{{ t("billing.downgrade_takes_effect_now") }}</p>
</div>
{% endif %}

<form hx-post="/ui/billing/change" class="mt-4 flex items-center gap-3">
  <input type="hidden" name="price_id" value="{{ target.price_id }}">
  <button type="submit" class="p-btn-primary">{{ t("billing.confirm_button") }}</button>
  <a class="p-btn-ghost" href="/billing">{{ t("common.cancel") }}</a>
</form>
{% endblock %}
```

The downgrade warning is not decoration. A downgrade reduces capacity
immediately while the money is credited at renewal, and the customer has to read
that before confirming, not after.

Add the new keys to all three catalogs, including
`billing.downgrade_takes_effect_now`: "Your new limits apply immediately. The
difference is credited to your next renewal."

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_billing_changes.py tests/test_i18n.py -q`
Expected: pass

- [ ] **Step 6: Commit**

```bash
git add app/billing/router.py app/templates/billing_confirm.html app/i18n/locales tests/test_billing_changes.py
git commit -m "feat(billing): confirm a plan change with Paddle's own figures"
```

---

### Task 3: Apply the change without lying about the result

**Files:**
- Modify: `app/billing/router.py`
- Test: `tests/test_billing_changes.py`

**Interfaces:**
- Produces: `POST /ui/billing/change`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_billing_changes.py

@pytest.mark.asyncio
async def test_change_calls_paddle_and_writes_nothing_locally(client: AsyncClient, db, monkeypatch):
    """The webhook is the only writer. A route that also wrote would create a
    second source of truth that disagrees the moment a payment declines."""
    from app.accounts.plans import subscription_for

    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    account, owner = await _paid_owner(db, monkeypatch)
    called = {}

    async def prices():
        return [_price("studio", "monthly", "pri_studio_m")]

    async def get_sub(subscription_id):
        return paddle.SubscriptionView(
            "active", "pri_solo_m", "solo", "monthly", None, None, None, None, None)

    async def change(subscription_id, price_id, proration):
        called["args"] = (subscription_id, price_id, proration)

    monkeypatch.setattr(paddle, "list_plan_prices", prices)
    monkeypatch.setattr(paddle, "get_subscription", get_sub)
    monkeypatch.setattr(paddle, "change_plan", change)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.post("/ui/billing/change", data={"price_id": "pri_studio_m"})
    assert r.status_code in (200, 204)
    assert called["args"][1:] == ("pri_studio_m", paddle.PRORATION_UPGRADE)

    db.expire_all()
    row = await subscription_for(db, account.id)
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

    async def boom(subscription_id, price_id, proration):
        raise paddle.PaddleError("card declined")

    monkeypatch.setattr(paddle, "list_plan_prices", prices)
    monkeypatch.setattr(paddle, "get_subscription", get_sub)
    monkeypatch.setattr(paddle, "change_plan", boom)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.post("/ui/billing/change", data={"price_id": "pri_studio_m"})
    assert r.status_code >= 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_billing_changes.py -q`
Expected: FAIL with 404

- [ ] **Step 3: Implement**

```python
from fastapi import Form
from fastapi.responses import Response

from app.ui.flash import flash_success


@router.post("/ui/billing/change")
async def billing_change(
    request: Request,
    price_id: str = Form(...),
    user: User = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Ask Paddle to change the plan, then say so honestly.

    Nothing about the plan is written here. The subscription id comes from the
    owner's own row rather than the request, so no owner can act on another
    tenant's subscription by guessing an id.
    """
    subscription = await plans.subscription_for(db, user.account_id)
    if subscription is None or not subscription.paddle_subscription_id:
        raise HTTPException(status_code=400, detail="no subscription to change")

    target = await _resolve_target(price_id)
    current = await paddle.get_subscription(subscription.paddle_subscription_id)
    proration = billing_service.proration_for(current, target)
    try:
        await paddle.change_plan(
            subscription.paddle_subscription_id, target.price_id, proration
        )
    except paddle.PaddleError as exc:
        logger.warning("plan change failed for account %s: %s", user.account_id, exc)
        raise HTTPException(status_code=502, detail="billing_provider_error") from exc

    # Deliberately not the new plan name: the webhook has not landed yet and
    # painting it now would contradict itself if the change did not stick.
    flash_success(request, "billing.change_submitted")
    return Response(status_code=204, headers={"HX-Refresh": "true"})
```

The form in `billing_confirm.html` already uses `hx-post`, which it must: a
plain form would dead-end on the 204.

Add `billing.change_submitted` to all three catalogs: "Change submitted. It
appears here in a few seconds."

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_billing_changes.py tests/test_i18n.py -q`
Expected: pass

- [ ] **Step 5: Commit**

```bash
git add app/billing/router.py app/i18n/locales tests/test_billing_changes.py
git commit -m "feat(billing): apply a plan change and report it honestly"
```

---

### Task 4: Cancellation

**Files:**
- Modify: `app/billing/router.py`, `app/templates/billing.html`
- Test: `tests/test_billing_changes.py`

**Interfaces:**
- Produces: `POST /ui/billing/cancel`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_billing_changes.py

@pytest.mark.asyncio
async def test_cancel_schedules_and_keeps_access(client: AsyncClient, db, monkeypatch):
    """Cancelling is not losing access today. The Terms promise the paid period."""
    from app.accounts.plans import subscription_for

    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    account, owner = await _paid_owner(db, monkeypatch)
    called = {}

    async def cancel(subscription_id):
        called["id"] = subscription_id

    monkeypatch.setattr(paddle, "cancel_subscription", cancel)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.post("/ui/billing/cancel")
    assert r.status_code in (200, 204)
    assert called["id"].startswith("sub_")

    db.expire_all()
    row = await subscription_for(db, account.id)
    assert (row.plan_code, row.status) == ("solo", "active")


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
    await client.post("/login", data={"email": member.email, "password": "secret-password"})

    r = await client.post("/ui/billing/cancel")
    assert r.status_code in (403, 404)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_billing_changes.py -q`
Expected: FAIL with 404

- [ ] **Step 3: Implement**

```python
@router.post("/ui/billing/cancel")
async def billing_cancel(
    request: Request,
    user: User = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> Response:
    subscription = await plans.subscription_for(db, user.account_id)
    if subscription is None or not subscription.paddle_subscription_id:
        raise HTTPException(status_code=400, detail="no subscription to cancel")
    try:
        await paddle.cancel_subscription(subscription.paddle_subscription_id)
    except paddle.PaddleError as exc:
        logger.warning("cancellation failed for account %s: %s", user.account_id, exc)
        raise HTTPException(status_code=502, detail="billing_provider_error") from exc

    flash_success(request, "billing.cancel_submitted")
    return Response(status_code=204, headers={"HX-Refresh": "true"})
```

In `app/templates/billing.html`, inside the current-plan section, when `detail`
exists and `detail.scheduled_action` is not `"cancel"`:

```jinja
<form hx-post="/ui/billing/cancel" class="mt-4">
  <button type="submit" class="p-btn-ghost">{{ t("billing.cancel_plan") }}</button>
</form>
```

Add `billing.cancel_submitted` ("Cancellation scheduled. Your plan stays active
until the end of the period you paid for.") and `billing.cancel_plan` to all
three catalogs.

- [ ] **Step 4: Add the change buttons to the plan cards**

In the plan-card loop from plan 03, replace the buy button for accounts that
already have a subscription:

```jinja
{% if detail %}
<a class="p-btn mt-3" href="/ui/billing/confirm?price_id={{ price.price_id }}">{{ t("billing.switch_to") }}</a>
{% endif %}
```

Add `billing.switch_to` to all three catalogs.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_billing_changes.py tests/test_billing_screen.py tests/test_billing_checkout.py tests/test_i18n.py -q`
Expected: pass

- [ ] **Step 6: Run the full gates**

```bash
ruff check app/ tests/
python -m mypy app/
TEST_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/pulsyr_test" \
  DEBUG=true SECRET_KEY=any-test-secret python -m pytest tests/ -q
```

- [ ] **Step 7: Commit**

```bash
git add app/billing app/templates app/i18n/locales tests/test_billing_changes.py
git commit -m "feat(billing): cancel at the end of the paid period"
```

---

### Task 5: Exercise every move in the sandbox

The unit tests mock Paddle. These five moves prove the contract.

- [ ] **Step 1: Upgrade** Solo monthly to Studio monthly. Expect an immediate
  charge on the confirmation screen, then `subscription.updated` delivered, then
  `plan_code = 'studio'`.
- [ ] **Step 2: Term switch** Studio monthly to Studio yearly. Expect a larger
  immediate charge.
- [ ] **Step 3: Downgrade** Studio yearly to Solo monthly. Expect no charge
  today, the downgrade warning shown, and the new limits applying at once.
- [ ] **Step 4: Cancel.** Expect status to stay `active` with a scheduled change,
  and `/billing` to read "Your plan ends on ...".
- [ ] **Step 5: Let the cancellation land.** Use the dashboard's subscription
  cancellation simulation with `effective_from: next_billing_period`. Expect
  `subscription.canceled`, and the account to drop to `plan_code = 'free'`,
  `status = 'active'`, with its projects and documents still present.

Record anything that differed from the plan in the spec's Open items section.
