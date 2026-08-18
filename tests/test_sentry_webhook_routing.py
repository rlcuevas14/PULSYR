"""Webhook Sentry tokenizado por cuenta: modos de auth + ruteo por slug + aislamiento."""
import hashlib
import hmac
import json
import uuid

import pytest
from sqlalchemy import select

from app.accounts.models import Account
from app.projects.models import Project
from app.webhooks import connection as sc
from app.webhooks.models import SentryIssue


async def _setup(db, sentry_slug="web", secret=None):
    a = Account(name="acc", slug=f"acc-{uuid.uuid4().hex[:6]}")
    db.add(a)
    await db.flush()
    p = Project(name="P", slug=f"p-{uuid.uuid4().hex[:6]}", account_id=a.id,
                sentry_project_slug=sentry_slug)
    db.add(p)
    await db.flush()
    from app.projects.modules import initialize_modules

    await initialize_modules(db, p.id, "product", "test:webhook")
    conn = await sc.get_or_create(db, a.id)
    if secret:
        conn.client_secret = secret
    await db.commit()
    return a, p, conn


def _payload(sid, slug="web"):
    return json.dumps({"data": {"issue": {"id": sid, "title": "Boom",
                                          "project": {"slug": slug}}}}).encode()


@pytest.mark.asyncio
async def test_unknown_token_404(client):
    r = await client.post("/webhooks/sentry/not-a-token", content=b"{}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_unsigned_mode_routes_by_slug(client, db):
    a, p, conn = await _setup(db)
    sid = f"R1{uuid.uuid4().hex[:8]}"
    r = await client.post(f"/webhooks/sentry/{conn.webhook_token}", content=_payload(sid))
    assert r.status_code == 200 and r.json()["created"] is True
    row = (await db.execute(select(SentryIssue).where(
        SentryIssue.sentry_issue_id == sid))).scalar_one()
    assert row.project_id == p.id and row.account_id == a.id


@pytest.mark.asyncio
async def test_signed_mode_verifies_hmac(client, db):
    _, _, conn = await _setup(db, secret="topsecret")
    body = _payload(f"R2{uuid.uuid4().hex[:8]}")
    sig = hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
    ok = await client.post(f"/webhooks/sentry/{conn.webhook_token}", content=body,
                           headers={"sentry-hook-signature": sig})
    assert ok.status_code == 200
    bad = await client.post(f"/webhooks/sentry/{conn.webhook_token}", content=body,
                            headers={"sentry-hook-signature": "forged"})
    assert bad.status_code == 401
    missing = await client.post(f"/webhooks/sentry/{conn.webhook_token}", content=body)
    assert missing.status_code == 401


@pytest.mark.asyncio
async def test_unmatched_slug_parks_with_account(client, db):
    a, _, conn = await _setup(db, sentry_slug="other")
    sid = f"R3{uuid.uuid4().hex[:8]}"
    r = await client.post(f"/webhooks/sentry/{conn.webhook_token}", content=_payload(sid))
    assert r.status_code == 200
    row = (await db.execute(select(SentryIssue).where(
        SentryIssue.sentry_issue_id == sid))).scalar_one()
    assert row.project_id is None and row.account_id == a.id


@pytest.mark.asyncio
async def test_cross_account_isolation(client, db):
    a1, p1, conn1 = await _setup(db)                      # cuenta 1 mapea "web"
    a2 = Account(name="a2", slug=f"a2-{uuid.uuid4().hex[:6]}")
    db.add(a2)
    await db.flush()
    p2 = Project(name="P2", slug=f"p2-{uuid.uuid4().hex[:6]}", account_id=a2.id,
                 sentry_project_slug="web")               # mismo slug sentry, cuenta 2
    db.add(p2)
    await db.commit()
    sid = f"R4{uuid.uuid4().hex[:8]}"
    r = await client.post(f"/webhooks/sentry/{conn1.webhook_token}", content=_payload(sid))
    assert r.status_code == 200
    row = (await db.execute(select(SentryIssue).where(
        SentryIssue.sentry_issue_id == sid))).scalar_one()
    assert row.project_id == p1.id      # gana la cuenta del token — nunca p2
    assert row.project_id != p2.id


@pytest.mark.asyncio
async def test_bad_json_and_missing_id_422(client, db):
    _, _, conn = await _setup(db)
    bad = await client.post(f"/webhooks/sentry/{conn.webhook_token}", content=b"not json")
    assert bad.status_code == 422
    noid = await client.post(f"/webhooks/sentry/{conn.webhook_token}",
                             content=json.dumps({"data": {"issue": {"title": "x"}}}).encode())
    assert noid.status_code == 422


@pytest.mark.asyncio
async def test_event_alert_shape_single_project_fallback(client, db):
    a, p, conn = await _setup(db, sentry_slug="web")      # único proyecto mapeado
    sid = f"R5{uuid.uuid4().hex[:8]}"
    body = json.dumps({"data": {"event": {"issue_id": sid, "title": "Alerted",
                                          "level": "error", "project": 7,
                                          "web_url": "https://s/e"}}}).encode()
    r = await client.post(f"/webhooks/sentry/{conn.webhook_token}", content=body)
    assert r.status_code == 200
    row = (await db.execute(select(SentryIssue).where(
        SentryIssue.sentry_issue_id == sid))).scalar_one()
    assert row.project_id == p.id and row.account_id == a.id  # fallback a único mapeado
