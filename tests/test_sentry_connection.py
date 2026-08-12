"""Tenancy-safe Sentry connection service (spec 2026-07-10)."""
import uuid

import pytest

from app.accounts.models import Account
from app.projects.models import Project
from app.webhooks import connection as sc
from app.webhooks.models import SentryIssue


async def _account(db, name="acme"):
    a = Account(name=name, slug=f"{name}-{uuid.uuid4().hex[:6]}")
    db.add(a)
    await db.flush()
    return a


async def _project(db, account, slug, sentry_slug=None):
    p = Project(
        name=slug, slug=f"{slug}-{uuid.uuid4().hex[:6]}", account_id=account.id,
        sentry_project_slug=sentry_slug,
    )
    db.add(p)
    await db.flush()
    return p


@pytest.mark.asyncio
async def test_get_or_create_generates_token_once(db):
    a = await _account(db)
    c1 = await sc.get_or_create(db, a.id)
    c2 = await sc.get_or_create(db, a.id)
    assert c1.id == c2.id and len(c1.webhook_token) >= 32
    assert await sc.get_by_token(db, c1.webhook_token) is not None
    assert await sc.get_by_token(db, "nope") is None


@pytest.mark.asyncio
async def test_update_validates_base_url_and_secrets_are_write_only(db):
    a = await _account(db)
    c = await sc.get_or_create(db, a.id)
    await sc.update_connection(db, c, client_secret=" s ", api_token="",
                               org_slug="org", base_url="https://sentry.example.com:9000")
    assert c.client_secret == "s" and c.api_token is None
    assert sc.effective_base_url(c) == "https://sentry.example.com:9000"
    with pytest.raises(sc.SentryConfigError):
        await sc.update_connection(db, c, client_secret="", api_token="",
                                   org_slug="", base_url="https://host/path")
    await sc.update_connection(db, c, client_secret="", api_token="new-token",
                               org_slug="", base_url="")
    assert c.client_secret == "s" and c.api_token == "new-token"
    await sc.update_connection(db, c, client_secret="", api_token="", org_slug="", base_url="",
                               clear_client_secret=True, clear_api_token=True)
    assert c.client_secret is None and c.api_token is None
    assert sc.effective_base_url(c) == sc.DEFAULT_BASE_URL


@pytest.mark.asyncio
async def test_regenerate_rotates(db):
    a = await _account(db)
    c = await sc.get_or_create(db, a.id)
    old = c.webhook_token
    await sc.regenerate_token(db, c)
    assert c.webhook_token != old
    assert await sc.get_by_token(db, old) is None


@pytest.mark.asyncio
async def test_outbound_requires_api_token(db):
    a = await _account(db)
    assert await sc.outbound(db, None) is None
    assert await sc.outbound(db, a.id) is None  # no connection yet
    c = await sc.get_or_create(db, a.id)
    assert await sc.outbound(db, a.id) is None  # connection but no token
    c.api_token = "tok"
    await db.flush()
    got = await sc.outbound(db, a.id)
    assert got is not None and got.id == c.id


@pytest.mark.asyncio
async def test_route_project_scoped_to_account(db):
    a1, a2 = await _account(db, "a1"), await _account(db, "a2")
    p1 = await _project(db, a1, "p1", sentry_slug="web")
    await _project(db, a2, "p2", sentry_slug="web")  # mismo slug sentry, otra cuenta
    hit = await sc.route_project(db, a1.id, "web")
    assert hit is not None and hit.id == p1.id
    assert await sc.route_project(db, a1.id, "unknown") is None
    # slug None → fallback al único proyecto mapeado de la cuenta
    only = await sc.route_project(db, a1.id, None)
    assert only is not None and only.id == p1.id
    await _project(db, a1, "p3", sentry_slug="api")
    assert await sc.route_project(db, a1.id, None) is None  # ahora ambiguo


@pytest.mark.asyncio
async def test_reattach_unmatched_is_tenancy_safe(db):
    a1, a2 = await _account(db, "a1"), await _account(db, "a2")
    p1 = await _project(db, a1, "p1", sentry_slug="web")
    db.add(SentryIssue(sentry_issue_id=f"U1-{uuid.uuid4().hex[:6]}".replace("-", "")[:20] + "1",
                       project="web", title="t", account_id=a1.id))
    db.add(SentryIssue(sentry_issue_id=f"U2{uuid.uuid4().hex[:8]}",
                       project="web", title="t", account_id=a2.id))
    db.add(SentryIssue(sentry_issue_id=f"U3{uuid.uuid4().hex[:8]}",
                       project="web", title="t"))  # legacy: sin cuenta
    await db.flush()
    assert await sc.count_unmatched(db, a1.id) == 2  # propia + legacy, NO la de a2
    n = await sc.reattach_unmatched(db, a1.id)
    assert n == 2
    from sqlalchemy import select
    rows = (await db.execute(
        select(SentryIssue).where(SentryIssue.project == "web")
    )).scalars().all()
    for r in rows:
        if r.account_id == a2.id:
            assert r.project_id is None  # la fila de a2 queda intacta
        else:
            assert r.project_id == p1.id and r.account_id == a1.id
