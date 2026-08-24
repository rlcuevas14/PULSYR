"""Checkout: the CSP it needs, the route Paddle links to, and the signup handoff."""

import base64
import json
import uuid
from datetime import datetime, timezone

import itsdangerous
import pytest
from httpx import AsyncClient

from app.accounts.plans import FREE, apply_paddle_subscription
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


async def _paid_owner_account(db, plan_code="solo"):
    """An owner account with a real Paddle subscription id, the way the webhook
    would leave it. Mirrors tests/test_billing_screen.py's helper of the same
    name so the router's `subscription.paddle_subscription_id` guard opens."""
    account, owner = await _owner_account(db)
    await apply_paddle_subscription(
        db, account_id=account.id, plan_code=plan_code, paddle_status="active",
        subscription_id=f"sub_{uuid.uuid4().hex[:12]}", customer_id="ctm_x",
        occurred_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
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


@pytest.mark.asyncio
async def test_billing_prefix_is_boundary_anchored(client: AsyncClient):
    """A path that merely starts with the same letters must not inherit the Paddle policy."""
    r = await client.get("/billingsomething")
    csp = r.headers["content-security-policy"]
    assert "paddle.com" not in csp
    assert r.headers["permissions-policy"].startswith("camera=(), microphone=(), geolocation=(), payment=()")


@pytest.mark.asyncio
async def test_checkout_page_needs_no_session(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "paddle_client_token", "test_abc")
    r = await client.get("/billing/checkout?_ptxn=txn_01m0rx2pt08422pp2tpfkw4f60")
    assert r.status_code == 200
    assert "txn_01m0rx2pt08422pp2tpfkw4f60" in r.text
    assert "test_abc" in r.text


@pytest.mark.asyncio
async def test_checkout_page_never_leaks_the_api_key(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "paddle_client_token", "test_abc")
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_secret")
    r = await client.get("/billing/checkout?_ptxn=txn_01m0rx2pt08422pp2tpfkw4f60")
    assert "pdl_sdbx_apikey_secret" not in r.text


@pytest.mark.asyncio
async def test_checkout_page_rejects_a_malformed_transaction_id(client: AsyncClient, monkeypatch):
    """The id goes straight into a script call, so it is validated, not trusted."""
    monkeypatch.setattr(settings, "paddle_client_token", "test_abc")
    r = await client.get("/billing/checkout?_ptxn=<script>alert(1)</script>")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_checkout_page_rejects_a_too_short_transaction_id(client: AsyncClient, monkeypatch):
    """Right prefix and charset, wrong length: the bound must still do work."""
    monkeypatch.setattr(settings, "paddle_client_token", "test_abc")
    r = await client.get("/billing/checkout?_ptxn=txn_123")
    assert r.status_code == 400


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


@pytest.mark.asyncio
async def test_paying_account_is_not_offered_a_second_checkout(client: AsyncClient, db, monkeypatch):
    """The one scenario the guard exists for: a subscription is already active
    AND the catalog loaded successfully. The plan card still shows (it is
    informational), the buy button must not."""
    from app.billing import paddle

    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    monkeypatch.setattr(settings, "paddle_client_token", "test_abc")
    _account, owner = await _paid_owner_account(db, plan_code="solo")

    async def prices():
        return [paddle.PlanPrice("pri_studio_m", "studio", "monthly", "2000", "USD")]

    async def fake_get_subscription(_subscription_id: str) -> paddle.SubscriptionView:
        return paddle.SubscriptionView(
            "active", "pri_solo_m", "solo", "monthly", None, None, None, None, None,
        )

    monkeypatch.setattr(paddle, "list_plan_prices", prices)
    monkeypatch.setattr(paddle, "get_subscription", fake_get_subscription)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.get("/billing")
    assert "Studio" in r.text
    assert "data-paddle-price" not in r.text


@pytest.mark.asyncio
async def test_paying_account_keeps_the_guard_when_the_live_read_fails(
    client: AsyncClient, db, monkeypatch,
):
    """The gap between the two tests around this one: a subscription is active,
    both Paddle settings are set and the catalog loads, but the live subscription
    read fails. `detail` is None for a reason that has nothing to do with whether
    the account pays us, so deciding on it would offer a paying customer a second
    checkout, and a second live subscription orphans the first."""
    from app.billing import paddle

    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    monkeypatch.setattr(settings, "paddle_client_token", "test_abc")
    _account, owner = await _paid_owner_account(db, plan_code="solo")

    async def prices():
        return [paddle.PlanPrice("pri_studio_m", "studio", "monthly", "2000", "USD")]

    async def boom(_subscription_id: str) -> paddle.SubscriptionView:
        raise paddle.PaddleError("down")

    monkeypatch.setattr(paddle, "list_plan_prices", prices)
    monkeypatch.setattr(paddle, "get_subscription", boom)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.get("/billing")
    assert r.status_code == 200
    assert "data-paddle-price" not in r.text


@pytest.mark.asyncio
async def test_catalog_error_still_renders_plan_and_usage(client: AsyncClient, db, monkeypatch):
    from app.billing import paddle

    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    monkeypatch.setattr(settings, "paddle_client_token", "test_abc")
    _account, owner = await _owner_account(db)

    async def boom():
        raise paddle.PaddleError("down")

    monkeypatch.setattr(paddle, "list_plan_prices", boom)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.get("/billing")
    assert r.status_code == 200
    assert "Free" in r.text or "Gratuito" in r.text
    assert "0.0 MB" in r.text


def _open_signup(monkeypatch):
    """/signup redirects to /setup with no users at all, and to /login with no
    OAuth provider configured: either redirect skips the intent-storing code
    before it runs, so both preconditions have to hold for the two tests below
    to exercise anything."""
    monkeypatch.setattr(settings, "public_signup", True)
    monkeypatch.setattr(settings, "oauth_github_client_id", "cid")
    monkeypatch.setattr(settings, "oauth_github_client_secret", "csecret")


def _session(client: AsyncClient) -> dict:
    """Starlette's signed session cookie, read straight from the jar.

    There used to be a `GET /billing/intent` route whose only callers were these
    tests: an unauthenticated production endpoint that existed to make a session
    key observable. Reading the cookie here costs one helper and removes the
    route from the attack surface.
    """
    raw = client.cookies.get("pulsyr_session")
    if raw is None:
        return {}
    payload = itsdangerous.TimestampSigner(str(settings.secret_key)).unsign(raw)
    return dict(json.loads(base64.b64decode(payload)))


@pytest.mark.asyncio
async def test_signup_remembers_a_valid_plan_choice(client: AsyncClient, db, monkeypatch):
    """The choice made on the public pricing page survives to the billing screen,
    where it preselects that plan's button rather than any other."""
    from app.billing import paddle

    _account, owner = await _owner_account(db)
    _open_signup(monkeypatch)
    await client.get("/signup?plan=solo&cycle=monthly")

    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_x")
    monkeypatch.setattr(settings, "paddle_client_token", "test_abc")

    async def prices():
        return [
            paddle.PlanPrice("pri_solo_m", "solo", "monthly", "800", "USD"),
            paddle.PlanPrice("pri_studio_m", "studio", "monthly", "2000", "USD"),
        ]

    monkeypatch.setattr(paddle, "list_plan_prices", prices)
    await client.post("/login", data={"email": owner.email, "password": "secret-password"})

    r = await client.get("/billing")
    assert r.status_code == 200
    solo = r.text.split('data-paddle-price="pri_solo_m"')[1].split("</button>")[0]
    studio = r.text.split('data-paddle-price="pri_studio_m"')[1].split("</button>")[0]
    assert "autofocus" in solo
    assert "autofocus" not in studio


@pytest.mark.asyncio
async def test_signup_ignores_an_unknown_plan(client: AsyncClient, db, monkeypatch):
    """A hand-edited query string must not put an unknown plan in the session.

    Asserted on the session rather than on /billing on purpose: the catalog only
    ever contains plans we enforce, so an "enterprise" intent could never match a
    price and the rendered page would look identical either way. The session is
    the only place where storing it and refusing it differ.
    """
    await _owner_account(db)
    _open_signup(monkeypatch)
    await client.get("/signup?plan=enterprise&cycle=monthly")
    assert "billing_intent" not in _session(client)
