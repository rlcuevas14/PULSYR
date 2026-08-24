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
