"""Checkout: the CSP it needs, the route Paddle links to, and the signup handoff."""

import uuid

import pytest
from httpx import AsyncClient

from app.accounts.plans import FREE
from app.accounts.service import create_account
from app.config import settings  # noqa: F401 (used once later tasks in this feature add more tests here)


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
