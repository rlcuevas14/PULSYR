"""Página de integración Sentry a nivel cuenta (solo owner) — spec 2026-07-10 §5/§6."""
import uuid

import pytest
from httpx import AsyncClient

from app.database import get_db


async def _owner_login(client: AsyncClient):
    from app.accounts.service import create_account
    s = uuid.uuid4().hex[:8]
    email = f"io{s}@t.cl"
    async for db in client.app.dependency_overrides[get_db]():
        acc, _owner = await create_account(db, f"acc{s}", email, "O", "password")
        await db.commit()
        acc_id = acc.id
        break
    r = await client.post("/login", data={"email": email, "password": "password"},
                          follow_redirects=False)
    assert r.status_code == 303
    return acc_id


@pytest.mark.asyncio
async def test_integrations_owner_only(client: AsyncClient):
    from app.accounts.members import create_member
    acc_id = await _owner_login(client)
    ok = await client.get("/account/integrations")
    assert ok.status_code == 200 and "/webhooks/sentry/" in ok.text

    # un member no-owner no entra
    s = uuid.uuid4().hex[:6]
    memail = f"mem{s}@t.cl"
    async for db in client.app.dependency_overrides[get_db]():
        await create_member(db, acc_id, memail, "M", "password")
        await db.commit()
        break
    await client.post("/logout", follow_redirects=False)
    await client.post("/login", data={"email": memail, "password": "password"},
                      follow_redirects=False)
    denied = await client.get("/account/integrations", follow_redirects=False)
    assert denied.status_code in (303, 403)


@pytest.mark.asyncio
async def test_integrations_save_regenerate_and_validation(client: AsyncClient):
    acc_id = await _owner_login(client)
    r = await client.post("/account/integrations", data={
        "client_secret": "cs", "api_token": "tok", "org_slug": "acme",
        "base_url": "https://sentry.acme.dev"}, follow_redirects=False)
    assert r.status_code == 303
    page1 = (await client.get("/account/integrations")).text
    assert "acme" in page1
    assert 'value="cs"' not in page1 and 'value="tok"' not in page1
    assert page1.count("Configured") >= 2
    preserve = await client.post("/account/integrations", data={
        "client_secret": "", "api_token": "", "org_slug": "acme",
        "base_url": "https://sentry.acme.dev"}, follow_redirects=False)
    assert preserve.status_code == 303
    async for db in client.app.dependency_overrides[get_db]():
        from app.webhooks.connection import get_for_account
        conn = await get_for_account(db, acc_id)
        assert conn is not None and conn.client_secret == "cs" and conn.api_token == "tok"
        break
    r2 = await client.post("/account/integrations/regenerate", follow_redirects=False)
    assert r2.status_code == 303
    page2 = (await client.get("/account/integrations")).text
    assert page1 != page2  # la URL del webhook rotó
    bad = await client.post("/account/integrations", data={
        "client_secret": "", "api_token": "", "org_slug": "",
        "base_url": "https://x/path"}, follow_redirects=False)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_integrations_reattach_unmatched(client: AsyncClient):
    from sqlalchemy import select

    from app.projects.models import Project
    from app.projects.service import create_project
    from app.webhooks.models import SentryIssue
    acc_id = await _owner_login(client)
    sid = f"UM{uuid.uuid4().hex[:8]}"
    async for db in client.app.dependency_overrides[get_db]():
        proj = (await db.execute(
            select(Project).where(Project.account_id == acc_id)
        )).scalars().first()
        if proj is None:  # create_account no crea proyecto por defecto
            proj = await create_project(db, name=f"p{uuid.uuid4().hex[:6]}", account_id=acc_id)
        proj.sentry_project_slug = "web"
        db.add(SentryIssue(sentry_issue_id=sid, project="web", title="t", account_id=acc_id))
        await db.commit()
        expected_pid = proj.id
        break
    page = (await client.get("/account/integrations")).text
    assert "reattach" in page.lower() or "re-attach" in page.lower() or "1" in page
    r = await client.post("/account/integrations/reattach", follow_redirects=False)
    assert r.status_code == 303
    async for db in client.app.dependency_overrides[get_db]():
        row = (await db.execute(select(SentryIssue).where(
            SentryIssue.sentry_issue_id == sid))).scalar_one()
        assert row.project_id == expected_pid
        break
