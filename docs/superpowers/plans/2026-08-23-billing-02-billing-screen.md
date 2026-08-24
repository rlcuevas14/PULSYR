# Billing 02: the billing screen

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An owner opens `/billing` and sees what the account pays, how close it is to its limits, whether a payment failed, and where to fix a card.

**Architecture:** Entitlements come from the local mirror, billing detail from a live Paddle read. The usage numbers are produced by the same queries the entitlement guards run, so the screen cannot claim something the server does not enforce. No action buttons yet: plans 3 and 4 add them.

**Tech Stack:** FastAPI, Jinja2, HTMX 2, Tailwind CDN with the `.p-*` component classes.

**Spec:** `docs/superpowers/specs/2026-08-23-billing-experience-design.md`
**Parent plan:** `docs/superpowers/plans/2026-08-23-billing-experience.md` (its Global Constraints apply to every task here)
**Depends on:** plan 01 (`app/billing/paddle.py`)

---

## File structure

| File | Responsibility |
|---|---|
| `app/accounts/plans.py` | Gains `Usage` and `usage_for`; the existing guards start using them |
| `app/billing/router.py` | `GET /billing` |
| `app/templates/billing.html` | The screen |
| `app/main.py` | Mount the router |
| `app/templates/base.html:71`, `app/templates/partials/_mobile_more_sheet.html:48` | Owner nav entry |
| `app/i18n/locales/{en,es,fr}.json` | Copy |
| `tests/test_billing_screen.py` | Access, rendering, degradation |

---

### Task 1: One source for the usage numbers

The guards already count projects, members and storage. The screen must show the
same numbers. Extracting the counts is what stops the two from drifting.

**Files:**
- Modify: `app/accounts/plans.py`
- Test: `tests/test_plans.py`

**Interfaces:**
- Produces: `Usage`, `usage_for(db, account_id) -> Usage`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_plans.py
from app.accounts.plans import usage_for


@pytest.mark.asyncio
async def test_usage_matches_what_the_guards_count(db):
    """The screen and the guard must never disagree about how full a plan is."""
    account, _owner = await _free_account(db)
    await create_project(db, name="First", account_id=account.id)
    await db.commit()

    usage = await usage_for(db, account.id)
    assert usage.projects == 1
    assert usage.members == 0
    assert usage.storage_bytes == 0

    with pytest.raises(PlanLimitError, match="projects"):
        await create_project(db, name="Second", account_id=account.id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plans.py::test_usage_matches_what_the_guards_count -q`
Expected: FAIL with `ImportError: cannot import name 'usage_for'`

- [ ] **Step 3: Extract the counts**

In `app/accounts/plans.py`, add above `ensure_project_capacity`:

```python
@dataclass(frozen=True)
class Usage:
    projects: int
    members: int
    storage_bytes: int


async def count_projects(db: AsyncSession, account_id: uuid.UUID) -> int:
    from app.projects.models import Project

    return int(await db.scalar(
        select(func.count()).select_from(Project).where(
            Project.account_id == account_id,
            Project.archived_at.is_(None),
        )
    ) or 0)


async def count_members(db: AsyncSession, account_id: uuid.UUID) -> int:
    from app.auth.models import User

    return int(await db.scalar(
        select(func.count()).select_from(User).where(
            User.account_id == account_id,
            User.account_role == "member",
            User.is_active.is_(True),
        )
    ) or 0)


async def used_storage_bytes(db: AsyncSession, account_id: uuid.UUID) -> int:
    from app.management.models import Deliverable, DeliverableVersion
    from app.projects.models import Project

    return int(await db.scalar(
        select(func.coalesce(func.sum(DeliverableVersion.size_bytes), 0))
        .select_from(DeliverableVersion)
        .join(Deliverable, Deliverable.id == DeliverableVersion.deliverable_id)
        .join(Project, Project.id == Deliverable.project_id)
        .where(Project.account_id == account_id)
    ) or 0)


async def usage_for(db: AsyncSession, account_id: uuid.UUID) -> Usage:
    return Usage(
        projects=await count_projects(db, account_id),
        members=await count_members(db, account_id),
        storage_bytes=await used_storage_bytes(db, account_id),
    )
```

Then rewrite the three guards to use them, keeping their `FOR UPDATE` plan read
exactly as it is. For example `ensure_project_capacity` becomes:

```python
async def ensure_project_capacity(db: AsyncSession, account_id: uuid.UUID) -> None:
    limits = limits_for(await active_plan_code(db, account_id, for_update=True))
    if limits.projects is None:
        return
    if await count_projects(db, account_id) >= limits.projects:
        raise PlanLimitError("projects", limits.projects)
```

Apply the same shape to `ensure_member_capacity` (using `count_members`) and
`ensure_storage_capacity` (using `used_storage_bytes` plus `additional_bytes`).
Leave `ensure_token_capacity` alone: it counts per project, not per account, and
the screen does not show it because paid plans do not cap tokens.

- [ ] **Step 4: Run the full plans suite**

Run: `python -m pytest tests/test_plans.py -q`
Expected: all pass, including the pre-existing limit tests

- [ ] **Step 5: Commit**

```bash
git add app/accounts/plans.py tests/test_plans.py
git commit -m "refactor(plans): one source for the usage counts the guards enforce"
```

---

### Task 2: The route

**Files:**
- Create: `app/billing/router.py`
- Modify: `app/main.py:325` (beside `app.include_router(webhooks_router)`)
- Test: `tests/test_billing_screen.py`

**Interfaces:**
- Consumes: `paddle.configured`, `paddle.get_subscription`, `plans.usage_for`, `plans.limits_for`, `plans.subscription_for`
- Produces: `GET /billing`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_billing_screen.py
"""The billing screen: who may see it, and what it is allowed to claim."""

import uuid

import pytest
from httpx import AsyncClient

from app.accounts.plans import FREE, SELF_HOSTED
from app.accounts.service import create_account
from app.config import settings


async def _owner_account(db, plan_code=FREE):
    suffix = uuid.uuid4().hex[:8]
    account, owner = await create_account(
        db, f"Bill {suffix}", f"bill-{suffix}@test.cl", "Owner", "secret-password",
        plan_code=plan_code,
    )
    await db.commit()
    return account, owner


async def _login(client: AsyncClient, email: str) -> None:
    await client.post("/login", data={"email": email, "password": "secret-password"})


@pytest.mark.asyncio
async def test_owner_sees_plan_and_usage(client: AsyncClient, db, monkeypatch):
    monkeypatch.setattr(settings, "paddle_api_key", "")
    account, owner = await _owner_account(db)
    await _login(client, owner.email)

    r = await client.get("/billing")
    assert r.status_code == 200
    assert "Free" in r.text or "Gratuito" in r.text


@pytest.mark.asyncio
async def test_self_hosted_has_no_billing_screen(client: AsyncClient, db, monkeypatch):
    """A self-hosted install must not show a page about paying us."""
    monkeypatch.setattr(settings, "paddle_api_key", "")
    account, owner = await _owner_account(db, plan_code=SELF_HOSTED)
    await _login(client, owner.email)

    assert (await client.get("/billing")).status_code == 404


@pytest.mark.asyncio
async def test_member_cannot_reach_billing(client: AsyncClient, db, monkeypatch):
    """Billing belongs to the account holder, not to a collaborator with
    editor grants on every project."""
    from app.accounts.members import create_member

    monkeypatch.setattr(settings, "paddle_api_key", "")
    account, _owner = await _owner_account(db)
    member = await create_member(
        db, account_id=account.id, email=f"m-{uuid.uuid4().hex[:6]}@test.cl",
        name="Member", password="secret-password",
    )
    await db.commit()
    await _login(client, member.email)

    assert (await client.get("/billing")).status_code in (403, 404)


@pytest.mark.asyncio
async def test_screen_renders_when_paddle_is_unreachable(client: AsyncClient, db, monkeypatch):
    """Plan and usage come from the mirror, so an outage at Paddle degrades the
    page instead of breaking it."""
    from app.billing import paddle

    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    account, owner = await _owner_account(db)
    await _login(client, owner.email)

    async def boom(_subscription_id: str):
        raise paddle.PaddleError("down")

    monkeypatch.setattr(paddle, "get_subscription", boom)
    r = await client.get("/billing")
    assert r.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_billing_screen.py -q`
Expected: FAIL, `GET /billing` returns 404 for every case

- [ ] **Step 3: Write the router**

Create `app/billing/router.py`:

```python
"""The owner-facing billing screen.

Nothing here writes a plan. Entitlements are read from the local mirror and
billing detail is read live from Paddle, because a pending cancellation copied
into our database is a pending cancellation that can go stale.
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from app.accounts import plans
from app.auth.deps import require_owner
from app.auth.models import User
from app.billing import paddle
from app.database import get_db
from app.templates_config import templates

logger = logging.getLogger("pulsyr.billing")

router = APIRouter(tags=["billing"])


@router.get("/billing", response_class=HTMLResponse)
async def billing_screen(
    request: Request,
    user: User = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    subscription = await plans.subscription_for(db, user.account_id)
    plan_code = subscription.plan_code if subscription else plans.SELF_HOSTED
    if plan_code == plans.SELF_HOSTED:
        raise HTTPException(status_code=404)

    limits = plans.limits_for(plan_code)
    usage = await plans.usage_for(db, user.account_id)

    detail = None
    detail_failed = False
    if paddle.configured() and subscription and subscription.paddle_subscription_id:
        try:
            detail = await paddle.get_subscription(subscription.paddle_subscription_id)
        except paddle.PaddleError:
            detail_failed = True
            logger.warning("billing detail unavailable for account %s", user.account_id)

    return templates.TemplateResponse(request, "billing.html", {
        "plan_code": plan_code,
        "status": subscription.status if subscription else "active",
        "limits": limits,
        "usage": usage,
        "detail": detail,
        "detail_failed": detail_failed,
        "actions_available": paddle.configured(),
    })
```

In `app/main.py`, beside the other includes:

```python
    from app.billing.router import router as billing_router
    app.include_router(billing_router)
```

- [ ] **Step 4: Write the template**

Create `app/templates/billing.html`. Mirror the structure and class vocabulary of
`app/templates/account_members.html`; read `app/templates/partials/_head.html`
first for the available `.p-*` classes and tokens. Every string uses `t(...)`.

```jinja
{% extends "base.html" %}
{% block content %}
<h1 class="text-2xl font-semibold mb-6">{{ t("billing.title") }}</h1>

{% if status == "active" and detail and detail.status == "past_due" %}
<div class="mb-6 bg-warning/10 border border-warning/30 text-warning-strong rounded-xl px-4 py-3 text-sm">
  <p class="font-medium">{{ t("billing.past_due_title") }}</p>
  <p>{{ t("billing.past_due_body") }}</p>
  {% if detail.update_payment_method_url %}
  <a class="p-btn mt-3" href="{{ detail.update_payment_method_url }}">{{ t("billing.update_card") }}</a>
  {% endif %}
</div>
{% endif %}

<section class="p-card p-5 mb-6">
  <p class="text-sm opacity-70">{{ t("billing.current_plan") }}</p>
  <p class="text-xl font-semibold">{{ t("plan." ~ plan_code) }}</p>
  {% if detail %}
    <p>{{ t("billing.term." ~ detail.billing_period) }}</p>
    {% if detail.scheduled_action == "cancel" and detail.scheduled_at %}
      <p>{{ t("billing.cancels_on", date=detail.scheduled_at | fecha) }}</p>
    {% elif detail.next_billed_at %}
      <p>{{ t("billing.next_billed_on", date=detail.next_billed_at | fecha) }}</p>
    {% endif %}
    {% if detail.update_payment_method_url %}
      <a class="p-btn-ghost mt-3" href="{{ detail.update_payment_method_url }}">{{ t("billing.payment_method") }}</a>
    {% endif %}
  {% elif detail_failed %}
    <p class="opacity-70">{{ t("billing.detail_unavailable") }}</p>
  {% endif %}
</section>

<section class="p-card p-5">
  <h2 class="font-semibold mb-4">{{ t("billing.usage") }}</h2>
  <dl class="grid sm:grid-cols-3 gap-4">
    <div>
      <dt class="text-sm opacity-70">{{ t("billing.projects") }}</dt>
      <dd>{{ usage.projects }}{% if limits.projects %} / {{ limits.projects }}{% endif %}</dd>
    </div>
    <div>
      <dt class="text-sm opacity-70">{{ t("billing.collaborators") }}</dt>
      <dd>{{ usage.members }}{% if limits.members is not none %} / {{ limits.members }}{% endif %}</dd>
    </div>
    <div>
      <dt class="text-sm opacity-70">{{ t("billing.storage") }}</dt>
      <dd>{{ (usage.storage_bytes / 1048576) | round(1) }} MB{% if limits.storage_bytes %} / {{ (limits.storage_bytes / 1048576) | round(0) | int }} MB{% endif %}</dd>
    </div>
  </dl>
</section>
{% endblock %}
```

- [ ] **Step 5: Add the copy to all three catalogs**

Add to `app/i18n/locales/en.json`, then the Spanish and French equivalents in
`es.json` and `fr.json`. Missing any one of the three fails `tests/test_i18n.py`.

```json
  "billing.title": "Billing",
  "billing.current_plan": "Current plan",
  "billing.usage": "Usage",
  "billing.projects": "Projects",
  "billing.collaborators": "Collaborators",
  "billing.storage": "Document storage",
  "billing.term.monthly": "Billed monthly",
  "billing.term.yearly": "Billed yearly",
  "billing.next_billed_on": "Next billed on {date}",
  "billing.cancels_on": "Your plan ends on {date}",
  "billing.payment_method": "Payment method and invoices",
  "billing.update_card": "Update payment method",
  "billing.detail_unavailable": "Billing detail is temporarily unavailable.",
  "billing.past_due_title": "Your last payment failed",
  "billing.past_due_body": "Your access continues while the card is retried. Updating it now avoids an interruption.",
  "plan.free": "Free",
  "plan.solo": "Solo",
  "plan.studio": "Studio",
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_billing_screen.py tests/test_i18n.py -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add app/billing/router.py app/templates/billing.html app/main.py app/i18n/locales tests/test_billing_screen.py
git commit -m "feat(billing): owner billing screen with plan, usage and card link"
```

---

### Task 3: Reach it from the navigation

**Files:**
- Modify: `app/templates/base.html:71`, `app/templates/partials/_mobile_more_sheet.html:48`
- Modify: `app/i18n/locales/{en,es,fr}.json`
- Test: `tests/test_billing_screen.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_billing_screen.py

@pytest.mark.asyncio
async def test_owner_navigation_links_to_billing_when_billing_is_configured(
    client: AsyncClient, db, monkeypatch
):
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    _account, owner = await _owner_account(db)
    await _login(client, owner.email)

    r = await client.get("/backlog")
    assert 'href="/billing"' in r.text


@pytest.mark.asyncio
async def test_self_hosted_install_never_shows_billing(client: AsyncClient, db, monkeypatch):
    """No Paddle key means this is somebody's own install. There is nobody to
    pay, so the entry does not exist."""
    monkeypatch.setattr(settings, "paddle_api_key", "")
    _account, owner = await _owner_account(db)
    await _login(client, owner.email)

    r = await client.get("/backlog")
    assert 'href="/billing"' not in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_billing_screen.py -q`
Expected: FAIL on `test_owner_navigation_links_to_billing_when_billing_is_configured`

- [ ] **Step 3: Add the instance-level flag**

The navigation renders on every page from `base.html`, which has no database
session. Keying the entry on the account's plan would mean a query per page view
to decide one link, so it keys on the instance instead: a Paddle key is what
distinguishes the hosted service from somebody's own install.

In `app/templates_config.py`, beside the existing `FREE_LIMITS` global (line 25):

```python
def billing_enabled() -> bool:
    """Read at render time, not at import time: a value frozen when the module
    loaded cannot follow a settings change, and every test that monkeypatches
    the key would be asserting against the value the process started with."""
    return bool(settings.paddle_api_key)


templates.env.globals["billing_enabled"] = billing_enabled
```

Then in both `app/templates/base.html:71` and
`app/templates/partials/_mobile_more_sheet.html:48`, beside the members entry:

```jinja
{% if billing_enabled() %}<a href="/billing" class="p-menu-item">{{ t("nav.billing") }}</a>{% endif %}
```

Note the call parentheses. A bare `{% if billing_enabled %}` tests the function
object, which is always truthy, and the self-hosted test would pass while the
link rendered for everyone.

placed inside the existing `{% if user.account_role == 'owner' %}` block, so it
inherits the owner check rather than repeating it.

Add `"nav.billing"` to all three catalogs: `"Billing"`, `"Facturación"`,
`"Facturation"`.

**Known wrinkle, do not fix here.** Account-level and instance-level do not line
up perfectly: on the hosted instance, an account still carrying the legacy
`self_hosted` plan from the `v0021` backfill sees the link and gets a 404 from
the route. That is the operator's own account and exactly one of them exists.
The fix is to move it onto a real plan, not to add a per-request query.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_billing_screen.py tests/test_i18n.py tests/test_navigation.py -q`
Expected: all pass

- [ ] **Step 5: Run the full gates**

```bash
ruff check app/ tests/
python -m mypy app/
```

- [ ] **Step 6: Commit**

```bash
git add app/templates app/templates_config.py app/i18n/locales tests/test_billing_screen.py
git commit -m "feat(billing): link billing from the owner navigation"
```
