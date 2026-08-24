# Billing 03: checkout

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Free account can become a paying one, and a customer whose card failed can pay from the link Paddle emails them.

**Architecture:** Paddle's overlay checkout, opened by Paddle.js in two places: from the plan cards on `/billing` for a first purchase, and on a deliberately session-free `/billing/checkout` that Paddle appends `?_ptxn=` to. The account is identified by `customData.account_id`, which is what lets the already-shipped webhook route the resulting subscription to the right tenant.

**Tech Stack:** Paddle.js v2 from `cdn.paddle.com`, FastAPI, Jinja2.

**Spec:** `docs/superpowers/specs/2026-08-23-billing-experience-design.md`
**Parent plan:** `docs/superpowers/plans/2026-08-23-billing-experience.md` (its Global Constraints apply to every task here)
**Depends on:** plans 01 and 02

---

## The blocker this plan has to clear first

`app/web_security.py:79-84` sends a strict Content-Security-Policy on every
response:

```
default-src 'self'; script-src 'self'; connect-src 'self'; frame-src 'self';
img-src 'self' data:; form-action 'self'
```

and `Permissions-Policy: payment=()`.

Under that policy Paddle.js cannot load, its iframe cannot render, its XHR
cannot leave, and the Payment Request API is switched off. The checkout would
fail with nothing in the server log, which is the worst way to fail.

The fix is a **per-path widening, never a global one**. The backlog, items,
management and MCP surfaces keep the strict policy they have today; only the
billing paths gain the Paddle origins. Task 1 does this.

---

## File structure

| File | Responsibility |
|---|---|
| `app/web_security.py` | Per-path CSP for billing routes only |
| `app/billing/router.py` | `GET /billing/checkout`, session-free |
| `app/templates/billing_checkout.html` | Paddle.js host page for `_ptxn` |
| `app/templates/partials/_paddle_js.html` | Shared Paddle.js bootstrap |
| `app/templates/billing.html` | Plan cards that open the overlay |
| `app/auth/router.py` | Remember `?plan=&cycle=` through signup |
| `tests/test_billing_checkout.py` | CSP, route access, handoff |

---

### Task 1: Widen the CSP for billing paths only

**Files:**
- Modify: `app/web_security.py:60-85`
- Test: `tests/test_billing_checkout.py`

**Interfaces:**
- Produces: `_PADDLE_ORIGINS`, `_billing_csp()`, and the path check inside `dispatch`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_billing_checkout.py
"""Checkout: the CSP it needs, the route Paddle links to, and the signup handoff."""

import uuid

import pytest
from httpx import AsyncClient

from app.accounts.plans import FREE
from app.accounts.service import create_account
from app.config import settings


async def _owner_account(db, plan_code=FREE):
    suffix = uuid.uuid4().hex[:8]
    account, owner = await create_account(
        db, f"Chk {suffix}", f"chk-{suffix}@test.cl", "Owner", "secret-password",
        plan_code=plan_code,
    )
    await db.commit()
    return account, owner


@pytest.mark.asyncio
async def test_billing_paths_allow_paddle(client: AsyncClient):
    r = await client.get("/billing/checkout")
    csp = r.headers["content-security-policy"]
    assert "https://cdn.paddle.com" in csp
    assert "frame-src 'self' https://*.paddle.com" in csp
    assert "connect-src 'self' https://*.paddle.com" in csp
    assert r.headers["permissions-policy"].startswith("camera=(), microphone=()")
    assert "payment=(self" in r.headers["permissions-policy"]


@pytest.mark.asyncio
async def test_other_paths_keep_the_strict_policy(client: AsyncClient):
    """Widening the policy for payments must not widen it for the backlog."""
    r = await client.get("/login")
    csp = r.headers["content-security-policy"]
    assert "paddle.com" not in csp
    assert "script-src 'self';" in csp
    assert r.headers["permissions-policy"].startswith("camera=(), microphone=(), geolocation=(), payment=()")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_billing_checkout.py -q`
Expected: FAIL, `/billing/checkout` is 404 and the CSP has no Paddle origins

- [ ] **Step 3: Implement the per-path policy**

In `app/web_security.py`, above the middleware class:

```python
# Only the billing paths get these. Paddle serves its script from a CDN, renders
# checkout in an iframe on its own origin, and calls home over XHR, none of which
# the default policy allows. Widening globally would hand every other screen a
# weaker policy for no reason.
_PADDLE_ORIGINS = "https://*.paddle.com https://*.paddle.io"
_PADDLE_SCRIPT = "https://cdn.paddle.com"
_BILLING_PREFIX = "/billing"


def _strict_csp() -> str:
    return (
        "default-src 'self'; "
        "base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; frame-src 'self'; manifest-src 'self'"
    )


def _billing_csp() -> str:
    return (
        "default-src 'self'; "
        "base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        f"form-action 'self' {_PADDLE_ORIGINS}; "
        f"script-src 'self' {_PADDLE_SCRIPT}; "
        "style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' data: {_PADDLE_ORIGINS}; "
        "font-src 'self'; "
        f"connect-src 'self' {_PADDLE_ORIGINS}; "
        f"frame-src 'self' {_PADDLE_ORIGINS}; manifest-src 'self'"
    )
```

Then in `dispatch`, replace the fixed header assignment with:

```python
        is_billing = request.url.path.startswith(_BILLING_PREFIX)
        headers["Content-Security-Policy"] = _billing_csp() if is_billing else _strict_csp()
```

and make the Permissions-Policy conditional in the same way:

```python
        payment = 'payment=(self "https://buy.paddle.com" "https://sandbox-buy.paddle.com")' if is_billing else "payment=()"
        headers["Permissions-Policy"] = (
            f"camera=(), microphone=(), geolocation=(), {payment}, usb=(), interest-cohort=()"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_billing_checkout.py tests/test_web_baseline.py -q`
Expected: pass. If `test_web_baseline.py` asserts the exact old header string,
update that assertion to the non-billing path's value; do not weaken it.

- [ ] **Step 5: Commit**

```bash
git add app/web_security.py tests/test_billing_checkout.py
git commit -m "feat(billing): allow Paddle origins on billing paths only"
```

---

### Task 2: The session-free checkout page

This is the URL configured in Paddle as the default payment link. Paddle appends
`?_ptxn=txn_...` to it and emails it when a payment needs retrying. It must not
require a session: the transaction id is the capability, the page shows nothing
of ours, and putting a login wall in front of a payment-recovery email loses the
recovery.

**Files:**
- Modify: `app/billing/router.py`
- Create: `app/templates/billing_checkout.html`, `app/templates/partials/_paddle_js.html`
- Test: `tests/test_billing_checkout.py`

**Interfaces:**
- Produces: `GET /billing/checkout`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_billing_checkout.py

@pytest.mark.asyncio
async def test_checkout_page_needs_no_session(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "paddle_client_token", "test_abc")
    r = await client.get("/billing/checkout?_ptxn=txn_123")
    assert r.status_code == 200
    assert "txn_123" in r.text
    assert "test_abc" in r.text


@pytest.mark.asyncio
async def test_checkout_page_never_leaks_the_api_key(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "paddle_client_token", "test_abc")
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_secret")
    r = await client.get("/billing/checkout?_ptxn=txn_123")
    assert "pdl_sdbx_apikey_secret" not in r.text


@pytest.mark.asyncio
async def test_checkout_page_rejects_a_malformed_transaction_id(client: AsyncClient, monkeypatch):
    """The id goes straight into a script call, so it is validated, not trusted."""
    monkeypatch.setattr(settings, "paddle_client_token", "test_abc")
    r = await client.get("/billing/checkout?_ptxn=<script>alert(1)</script>")
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_billing_checkout.py -q`
Expected: FAIL with 404 on the new route

- [ ] **Step 3: Implement the route**

Add to `app/billing/router.py`:

```python
import re

from fastapi import Query

_TXN_RE = re.compile(r"^txn_[a-z0-9]{20,32}$")


@router.get("/billing/checkout", response_class=HTMLResponse)
async def billing_checkout(
    request: Request, _ptxn: str = Query(default=""),
) -> HTMLResponse:
    """Paddle's default payment link target. Deliberately session-free: this is
    where a payment-recovery email lands, and the transaction id is the
    capability. The page renders nothing belonging to the account."""
    if _ptxn and not _TXN_RE.match(_ptxn):
        raise HTTPException(status_code=400, detail="invalid transaction id")
    return templates.TemplateResponse(request, "billing_checkout.html", {
        "transaction_id": _ptxn,
        "paddle_token": settings.paddle_client_token,
        "paddle_environment": settings.paddle_environment,
    })
```

Import `settings` from `app.config` at the top of the router.

- [ ] **Step 4: Write the Paddle.js partial**

Create `app/templates/partials/_paddle_js.html`. The CSP forbids inline script,
so configuration travels in data attributes and the behaviour lives in a
self-hosted file.

```jinja
<script src="https://cdn.paddle.com/paddle/v2/paddle.js"></script>
<script src="/static/paddle-checkout.js"
        data-paddle-token="{{ paddle_token }}"
        data-paddle-environment="{{ paddle_environment }}"
        {% if transaction_id %}data-paddle-transaction="{{ transaction_id }}"{% endif %}
        defer></script>
```

Create `app/static/paddle-checkout.js`:

```javascript
// Reads its configuration from the script tag's data attributes: the app's CSP
// has no inline-script exception and this file is not the place to add one.
(function () {
  var el = document.currentScript;
  if (!el || !window.Paddle) return;
  var token = el.getAttribute("data-paddle-token");
  if (!token) return;
  Paddle.Environment.set(el.getAttribute("data-paddle-environment") || "sandbox");
  Paddle.Initialize({ token: token });

  var txn = el.getAttribute("data-paddle-transaction");
  if (txn) {
    Paddle.Checkout.open({ transactionId: txn });
    return;
  }

  document.querySelectorAll("[data-paddle-price]").forEach(function (button) {
    button.addEventListener("click", function () {
      Paddle.Checkout.open({
        items: [{ priceId: button.getAttribute("data-paddle-price"), quantity: 1 }],
        customData: { account_id: button.getAttribute("data-account-id") },
        customer: { email: button.getAttribute("data-email") || undefined },
        settings: { successUrl: window.location.origin + "/billing" },
      });
    });
  });
})();
```

Note: `document.currentScript` is null inside a `defer`red script's callbacks but
valid during initial execution, which is where it is read here.

Create `app/templates/billing_checkout.html`. It extends **`auth_base.html`**, not
`base.html`: this page is deliberately session-free, and `base.html` reads `user.*`
unconditionally, so extending it would 500 for exactly the visitor this page exists
to serve. `auth_base.html` is the shell the login page already uses for the same
reason, and it includes `partials/_head.html` so the styling still applies.

```jinja
{% extends "auth_base.html" %}
{% block title %}{{ t("billing.checkout_title") }}{% endblock %}
{% block tagline %}{{ t("billing.checkout_body") }}{% endblock %}
{% block card %}
{% include "partials/_paddle_js.html" %}
{% endblock %}
```

Add `billing.checkout_title` and `billing.checkout_body` to all three catalogs.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_billing_checkout.py tests/test_i18n.py -q`
Expected: pass

- [ ] **Step 6: Commit**

```bash
git add app/billing/router.py app/templates app/static/paddle-checkout.js app/i18n/locales tests/test_billing_checkout.py
git commit -m "feat(billing): session-free checkout page for Paddle payment links"
```

---

### Task 3: Buy a plan from the billing screen

**Files:**
- Modify: `app/templates/billing.html`, `app/billing/router.py`
- Test: `tests/test_billing_checkout.py`

**Interfaces:**
- Consumes: `paddle.list_plan_prices`
- Produces: plan cards carrying `data-paddle-price` and `data-account-id`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_billing_checkout.py

@pytest.mark.asyncio
async def test_free_account_is_offered_the_paid_prices(client: AsyncClient, db, monkeypatch):
    from app.billing import paddle

    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    monkeypatch.setattr(settings, "paddle_client_token", "test_abc")
    account, owner = await _owner_account(db)

    async def prices():
        return [
            paddle.PlanPrice("pri_solo_m", "solo", "monthly", "800", "USD"),
            paddle.PlanPrice("pri_studio_m", "studio", "monthly", "2000", "USD"),
        ]

    monkeypatch.setattr(paddle, "list_plan_prices", prices)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.get("/billing")
    assert 'data-paddle-price="pri_solo_m"' in r.text
    assert f'data-account-id="{account.id}"' in r.text


@pytest.mark.asyncio
async def test_no_client_token_means_no_buy_buttons(client: AsyncClient, db, monkeypatch):
    monkeypatch.setattr(settings, "paddle_api_key", "")
    monkeypatch.setattr(settings, "paddle_client_token", "")
    _account, owner = await _owner_account(db)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.get("/billing")
    assert r.status_code == 200
    assert "data-paddle-price" not in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_billing_checkout.py -q`
Expected: FAIL, the screen renders no plan cards

- [ ] **Step 3: Load the catalog in the route**

In `billing_screen`, after the usage lookup:

```python
    prices: list[paddle.PlanPrice] = []
    if paddle.configured() and settings.paddle_client_token:
        try:
            prices = await paddle.list_plan_prices()
        except paddle.PaddleError:
            logger.warning("plan catalog unavailable for account %s", user.account_id)
```

Add to the template context: `"prices": prices`, `"account_id": str(user.account_id)`,
`"user_email": user.email`, `"paddle_token": settings.paddle_client_token`,
`"paddle_environment": settings.paddle_environment`, `"transaction_id": ""`.

- [ ] **Step 4: Render the cards**

Append to `app/templates/billing.html`, before `{% endblock %}`:

```jinja
{% if prices %}
<section class="p-card p-5 mt-6">
  <h2 class="font-semibold mb-4">{{ t("billing.available_plans") }}</h2>
  <div class="grid sm:grid-cols-2 gap-4">
    {% for price in prices %}
      {% if price.plan_code != plan_code %}
      <article class="p-card p-4">
        <p class="font-semibold">{{ t("plan." ~ price.plan_code) }}</p>
        <p class="text-sm opacity-70">{{ t("billing.term." ~ price.billing_period) }}</p>
        {% if not detail %}
        <button type="button" class="p-btn mt-3"
                data-paddle-price="{{ price.price_id }}"
                data-account-id="{{ account_id }}"
                data-email="{{ user_email }}">{{ t("billing.choose_plan") }}</button>
        {% endif %}
      </article>
      {% endif %}
    {% endfor %}
  </div>
</section>
{% include "partials/_paddle_js.html" %}
{% endif %}
```

The `{% if not detail %}` guard keeps the buy button off accounts that already
have a subscription: those change plan through plan 04, not through a second
checkout.

Add `billing.available_plans` and `billing.choose_plan` to all three catalogs.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_billing_checkout.py tests/test_billing_screen.py tests/test_i18n.py -q`
Expected: pass

- [ ] **Step 6: Commit**

```bash
git add app/billing app/templates app/i18n/locales tests/test_billing_checkout.py
git commit -m "feat(billing): open Paddle checkout from the plan cards"
```

---

### Task 4: Carry the plan choice through signup

The public pricing page links to `/signup?plan=solo&cycle=monthly`. Today those
parameters are ignored.

**Files:**
- Modify: `app/auth/router.py` (the signup entry point and the OAuth callback)
- Modify: `app/billing/router.py`
- Test: `tests/test_billing_checkout.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_billing_checkout.py

@pytest.mark.asyncio
async def test_signup_remembers_a_valid_plan_choice(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "public_signup", True)
    await client.get("/signup?plan=solo&cycle=monthly")
    r = await client.get("/billing/intent")
    assert r.json() == {"plan": "solo", "cycle": "monthly"}


@pytest.mark.asyncio
async def test_signup_ignores_an_unknown_plan(client: AsyncClient, monkeypatch):
    """A hand-edited query string must not put an unknown plan in the session."""
    monkeypatch.setattr(settings, "public_signup", True)
    await client.get("/signup?plan=enterprise&cycle=monthly")
    r = await client.get("/billing/intent")
    assert r.json() == {"plan": None, "cycle": None}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_billing_checkout.py -q`
Expected: FAIL with 404 on `/billing/intent`

- [ ] **Step 3: Store the intent**

In `app/auth/router.py`, in the handler that serves `/signup`, before rendering:

```python
    from app.accounts.plans import PAID_LIMITS

    plan = request.query_params.get("plan", "")
    cycle = request.query_params.get("cycle", "")
    if plan in PAID_LIMITS and cycle in ("monthly", "yearly"):
        request.session["billing_intent"] = {"plan": plan, "cycle": cycle}
```

The session survives the OAuth round trip, so the callback needs no change.

- [ ] **Step 4: Expose and consume the intent**

Add to `app/billing/router.py`:

```python
@router.get("/billing/intent")
async def billing_intent(request: Request) -> dict[str, str | None]:
    """What the visitor picked on the public pricing page, if anything. Read by
    the billing screen to preselect a plan, and by the test suite."""
    intent = request.session.get("billing_intent") or {}
    return {"plan": intent.get("plan"), "cycle": intent.get("cycle")}
```

In `billing_screen`, read the same session key and pass `preselected_price_id`
to the template by matching the intent against the loaded `prices`, then mark
that card with `autofocus` on its button. Clear the key once consumed:

```python
    intent = request.session.pop("billing_intent", None) or {}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_billing_checkout.py tests/test_auth.py -q`
Expected: pass

- [ ] **Step 6: Run the full gates**

```bash
ruff check app/ tests/
python -m mypy app/
```

- [ ] **Step 7: Commit**

```bash
git add app/auth/router.py app/billing/router.py app/templates tests/test_billing_checkout.py
git commit -m "feat(billing): carry the pricing-page plan choice through signup"
```

---

### Task 5: Buy something in the sandbox

- [ ] **Step 1: Complete a real sandbox purchase**

With the sandbox keys exported and `app.pulsyr.dev` reachable over TLS, open
`/billing` as an owner of a Free account and buy Solo monthly using Paddle's test
card `4242 4242 4242 4242`, any future expiry, any CVC.

- [ ] **Step 2: Confirm the chain end to end**

- The overlay opens and completes.
- Paddle's notification log shows `subscription.created` delivered with a 200.
- `account_subscriptions` for that account now reads `plan_code = 'solo'`,
  `status = 'active'`, with both Paddle ids and `paddle_event_at` set.
- `/billing` shows Solo, the next billing date, and a payment-method link.

- [ ] **Step 3: Record what the browser console reported**

Any CSP violation printed there names an origin Task 1 did not anticipate. Add
only that origin to `_PADDLE_ORIGINS` or `_PADDLE_SCRIPT`, re-run
`tests/test_billing_checkout.py`, and commit.

```bash
git add app/web_security.py tests/test_billing_checkout.py
git commit -m "fix(billing): allow the Paddle origin the sandbox checkout actually used"
```
