# Billing Experience Implementation Plan (parent)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement each child plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a hosted account buy, change and cancel a paid plan from inside Pulsyr, with Paddle as the only source of truth for what is being paid.

**Architecture:** A new `app/billing/` module holds an outbound Paddle client and the owner-only billing screen. The screen reads entitlements from the local mirror (`account_subscriptions`) and billing detail live from the Paddle API. No route in this plan ever writes `plan_code`: the already-shipped webhook is the sole writer.

**Tech Stack:** FastAPI, httpx, Jinja2 + HTMX 2, Paddle Billing API (`2025-03-26`-era REST), Paddle.js overlay checkout.

**Spec:** `docs/superpowers/specs/2026-08-23-billing-experience-design.md`

---

## Global Constraints

Every task in every child plan inherits these.

- **The UI never decides a plan.** Routes call Paddle and return; `apply_paddle_subscription` in `app/accounts/plans.py`, invoked from the webhook, is the only writer of `plan_code`, `status` and the Paddle ids.
- **Never compute proration locally.** The figures shown to a customer come from `subscriptions.preview`, verbatim. The preview accounts for tax, currency and credit balances we do not track.
- **Upgrade:** `proration_billing_mode: "prorated_immediately"`, `on_payment_failure: "prevent_change"`. **Downgrade and term shortening:** `prorated_next_billing_period`. **Cancel:** `effective_from: "next_billing_period"`.
- **Owner only.** Every billing route depends on `require_owner` (`app/auth/deps.py:136`). The single exception is `GET /billing/checkout`, which is deliberately session-free.
- **Self-hosted sees nothing.** When the effective plan is `self_hosted`, `/billing` returns 404 and the nav entry does not render.
- **Degrades with no key.** With `PADDLE_API_KEY` empty the module imports, the screen renders plan and usage, and every action is hidden. Same contract as `app/ai/llm.py`.
- **i18n:** no user-visible string is hardcoded. Keys go in all three of `app/i18n/locales/{en,es,fr}.json`; `tests/test_i18n.py` fails CI if one is missing. Never name a Jinja loop variable `t`.
- **Design system:** only tokens and `.p-*` classes from `app/templates/partials/_head.html`. No gray/blue palette classes, no opacity modifiers on semantic tokens. Success feedback via `flash_success` (`app/ui/flash.py`). A form posting to a handler that returns `204 + HX-Refresh` must use `hx-post`.
- **Secrets:** `PADDLE_API_KEY` never reaches a template. `PADDLE_CLIENT_TOKEN` is public by design and does.
- **Gates before every commit:** `ruff check app/ tests/`, `python -m mypy app/`, and the tests for the touched area. Run pytest with `TEST_DATABASE_URL` set and `DEBUG=true`; never run two pytest sessions at once against `pulsyr_test`.

## Child plans, in dependency order

| # | Plan | Delivers | Independently useful? |
|---|---|---|---|
| 1 | `2026-08-23-billing-01-paddle-client.md` | `app/billing/paddle.py`, config, the five typed calls | No user-visible change; every later plan depends on its signatures |
| 2 | `2026-08-23-billing-02-billing-screen.md` | `GET /billing`: current plan, usage against limits, `past_due` banner, payment-method link | Yes. An owner can see what they pay and fix a card |
| 3 | `2026-08-23-billing-03-checkout.md` | Public `/billing/checkout`, Free to paid overlay, `/signup?plan=&cycle=` handoff | Yes. Money can be taken |
| 4 | `2026-08-23-billing-04-plan-changes.md` | Preview, confirm, upgrade, downgrade, term switch, cancel | Yes. A customer can self-serve every change |

**i18n and tests are not child plans.** They are constraints inside every task above. Splitting them into their own plan is how translations and coverage get deferred forever, and `tests/test_i18n.py` would fail the moment plan 2 landed without them.

## Interfaces that cross plan boundaries

Plan 1 produces these. Plans 2 through 4 consume them and must not redefine them.

```python
# app/billing/paddle.py

class PaddleError(RuntimeError): ...
class PaddleNotConfigured(PaddleError): ...

@dataclass(frozen=True)
class PlanPrice:
    price_id: str
    plan_code: str          # "solo" | "studio"
    billing_period: str     # "monthly" | "yearly"
    amount: str             # lowest denomination, e.g. "800"
    currency_code: str      # "USD"

@dataclass(frozen=True)
class SubscriptionView:
    status: str                              # Paddle's own status
    price_id: str
    plan_code: str
    billing_period: str
    next_billed_at: datetime | None
    scheduled_action: str | None             # "cancel" | "pause" | None
    scheduled_at: datetime | None
    update_payment_method_url: str | None
    cancel_url: str | None

@dataclass(frozen=True)
class ChangePreview:
    immediate_amount: str | None             # None when nothing is charged today
    recurring_amount: str
    currency_code: str
    next_billed_at: datetime | None

async def list_plan_prices() -> list[PlanPrice]: ...
async def get_subscription(subscription_id: str) -> SubscriptionView: ...
async def preview_change(subscription_id: str, price_id: str, proration: str) -> ChangePreview: ...
async def change_plan(subscription_id: str, price_id: str, proration: str) -> None: ...
async def cancel_subscription(subscription_id: str) -> None: ...
def configured() -> bool: ...
```

Plan 2 additionally produces, in `app/accounts/plans.py`:

```python
@dataclass(frozen=True)
class Usage:
    projects: int
    members: int
    storage_bytes: int

async def usage_for(db: AsyncSession, account_id: uuid.UUID) -> Usage: ...
```

## Two refinements to the spec, discovered from the API contract

1. **No portal session call.** The spec listed `portal_session` as a fifth client call. The subscription entity already carries `management_urls.update_payment_method` and `management_urls.cancel`, so the payment-method link comes free with the subscription read. The fifth call is `list_plan_prices` instead, which the screen needs to know which price ids it may switch to.
2. **Prices are discovered, not configured.** There are no `PADDLE_PRICE_*` environment variables. `list_plan_prices` reads the catalog and maps each price to a plan through `custom_data.plan_code`, the same field the webhook already trusts. Adding a tier never requires a redeploy.

## Verification

```bash
ruff check app/ tests/
python -m mypy app/
TEST_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/pulsyr_test" \
  DEBUG=true SECRET_KEY=any-test-secret python -m pytest tests/ -q
```

CI is the real gate. If local failures look impossible, reset the test schema
(`DROP SCHEMA public CASCADE; CREATE SCHEMA public;`) before debugging further.

## Out of scope

Invoices generated by Pulsyr (forbidden by Paddle MSA clause 10.1), per-seat
quantity billing, coupons, trials, dunning email, and metering of AI operations.
The last one is a published promise with no enforcement and needs its own spec.
