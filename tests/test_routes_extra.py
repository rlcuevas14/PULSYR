"""Coverage for auth/projects/accounts routers via real sessions + token-auth REST."""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.database import get_db


async def _owner(client: AsyncClient):
    """Owner+superadmin session (cookie in jar). Returns (user_id, project_id, project_slug)."""
    from app.auth.service import create_user
    from app.projects.models import Project

    suffix = uuid.uuid4().hex[:8]
    email = f"own{suffix}@t.cl"
    async for db in client.app.dependency_overrides[get_db]():
        user = await create_user(db, email, "Owner", "password", "admin")
        proj = (await db.execute(
            select(Project).where(Project.account_id == user.account_id)
        )).scalars().first()
        out = (user.id, proj.id, proj.slug)
        break
    r = await client.post("/login", data={"email": email, "password": "password"},
                          follow_redirects=False)
    assert r.status_code == 303
    return out


@pytest.mark.asyncio
async def test_login_failure_and_logout(client: AsyncClient):
    await _owner(client)
    logout = await client.post("/logout", follow_redirects=False)
    assert logout.status_code == 303
    bad = await client.post("/login", data={"email": "nobody@t.cl", "password": "x"},
                            follow_redirects=False)
    assert bad.status_code == 401


@pytest.mark.asyncio
async def test_project_settings_and_tokens(client: AsyncClient):
    _uid, _pid, slug = await _owner(client)
    # settings page
    s = await client.get(f"/projects/{slug}/settings")
    assert s.status_code == 200
    # update project metadata
    up = await client.post(f"/projects/{slug}/settings",
                           data={"name": "Renamed", "description": "d"}, follow_redirects=False)
    assert up.status_code == 303
    # mint a write token, then it appears, then revoke it
    tok = await client.post(f"/projects/{slug}/tokens",
                            data={"token_name": "ci", "scopes": "write"}, follow_redirects=False)
    assert tok.status_code == 303
    page = await client.get(f"/projects/{slug}/settings")
    assert "claude mcp add" in page.text
    from app.auth.models import ApiToken
    async for db in client.app.dependency_overrides[get_db]():
        tid = (await db.execute(select(ApiToken.id))).scalars().first()
        break
    rev = await client.post(f"/projects/{slug}/tokens/{tid}/revoke", follow_redirects=False)
    assert rev.status_code == 303


@pytest.mark.asyncio
async def test_project_sentry_slug_unique_per_account(client: AsyncClient):
    _uid, _pid, slug = await _owner(client)
    ok = await client.post(f"/projects/{slug}/settings",
                           data={"name": "P1", "sentry_project_slug": "web"},
                           follow_redirects=False)
    assert ok.status_code == 303
    # segundo proyecto de la misma cuenta no puede reclamar el mismo slug
    r = await client.post("/projects/new", data={"name": f"Second {uuid.uuid4().hex[:6]}"},
                          follow_redirects=False)
    assert r.status_code == 303
    slug2 = r.headers["location"].split("/projects/")[1].split("/")[0]
    dup = await client.post(f"/projects/{slug2}/settings",
                            data={"name": "P2", "sentry_project_slug": "web"},
                            follow_redirects=False)
    assert dup.status_code == 422
    # slug distinto sí pasa
    ok2 = await client.post(f"/projects/{slug2}/settings",
                            data={"name": "P2", "sentry_project_slug": "api"},
                            follow_redirects=False)
    assert ok2.status_code == 303


@pytest.mark.asyncio
async def test_project_create_and_404(client: AsyncClient):
    await _owner(client)
    r = await client.post("/projects/new", data={"name": f"Proj {uuid.uuid4().hex[:6]}"},
                          follow_redirects=False)
    assert r.status_code == 303
    missing = await client.get("/projects/does-not-exist/settings")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_superadmin_accounts(client: AsyncClient):
    await _owner(client)
    page = await client.get("/admin/accounts")
    assert page.status_code == 200
    created = await client.post("/admin/accounts", data={
        "name": "Acme Inc", "owner_name": "Boss",
        "owner_email": f"boss{uuid.uuid4().hex[:6]}@acme.cl", "password": "password1",
    }, follow_redirects=False)
    assert created.status_code == 303
    # duplicate email -> 422 re-render
    dup = await client.post("/admin/accounts", data={
        "name": "X", "owner_name": "Y", "owner_email": "dup@d.cl", "password": "password1",
    })
    dup2 = await client.post("/admin/accounts", data={
        "name": "X2", "owner_name": "Y", "owner_email": "dup@d.cl", "password": "password1",
    })
    assert dup.status_code == 303 and dup2.status_code == 422
    # toggle the first account active flag
    from app.accounts.models import Account
    async for db in client.app.dependency_overrides[get_db]():
        acc_id = (await db.execute(select(Account.id))).scalars().first()
        break
    tog = await client.post(f"/admin/accounts/{acc_id}/active", data={"active": "false"},
                            follow_redirects=False)
    assert tog.status_code == 303


@pytest.mark.asyncio
async def test_member_matrix_and_grants(client: AsyncClient):
    _uid, pid, _slug = await _owner(client)
    page = await client.get("/account/members")
    assert page.status_code == 200
    # create a collaborator
    memail = f"mem{uuid.uuid4().hex[:6]}@t.cl"
    cr = await client.post("/account/members",
                           data={"name": "Mem", "email": memail, "password": "password1"},
                           follow_redirects=False)
    assert cr.status_code == 303
    # short password -> 422
    bad = await client.post("/account/members",
                            data={"name": "M", "email": f"x{uuid.uuid4().hex[:5]}@t.cl", "password": "x"})
    assert bad.status_code == 422
    from app.auth.models import User
    async for db in client.app.dependency_overrides[get_db]():
        mid = (await db.execute(select(User.id).where(User.email == memail))).scalars().first()
        break
    # grant editor, then remove
    g = await client.post("/account/members/grant",
                          data={"user_id": str(mid), "project_id": str(pid), "role": "editor"},
                          follow_redirects=False)
    assert g.status_code == 303
    rm = await client.post("/account/members/grant",
                           data={"user_id": str(mid), "project_id": str(pid), "role": "none"},
                           follow_redirects=False)
    assert rm.status_code == 303


@pytest.mark.asyncio
async def test_rest_with_token_resolves_project(client: AsyncClient):
    """REST under a Bearer token resolves the project from token.project_id (access.py)."""
    from app.accounts.service import create_account
    from app.auth.service import create_api_token
    from app.projects.service import create_project

    suffix = uuid.uuid4().hex[:8]
    async for db in client.app.dependency_overrides[get_db]():
        acc, owner = await create_account(db, f"a{suffix}", f"tok{suffix}@t.cl", "T", "password")
        proj = await create_project(db, name=f"p{suffix}", account_id=acc.id)
        tok, raw = await create_api_token(db, "rest", "write", owner.id)
        tok.project_id = proj.id
        await db.commit()
        break
    hdr = {"Authorization": f"Bearer {raw}"}
    # create a scope then list — both scoped to the token's project
    cr = await client.post("/api/v1/scopes", json={"name": f"area-{suffix}"}, headers=hdr)
    assert cr.status_code == 201
    ls = await client.get("/api/v1/scopes", headers=hdr)
    assert ls.status_code == 200 and any(s["name"] == f"area-{suffix}" for s in ls.json())


@pytest.mark.asyncio
async def test_member_viewer_cannot_write(client: AsyncClient):
    """A viewer-granted member reads but cannot perform write actions (access.py guard)."""
    from app.accounts.members import create_member, set_grant
    from app.accounts.service import create_account
    from app.items.models import Item
    from app.projects.service import create_project
    from app.scopes.models import Scope

    s = uuid.uuid4().hex[:6]
    memail = f"vw{s}@t.cl"
    async for db in client.app.dependency_overrides[get_db]():
        acc, _owner = await create_account(db, f"a{s}", f"o{s}@t.cl", "O", "password")
        proj = await create_project(db, name=f"p{s}", account_id=acc.id)
        member = await create_member(db, acc.id, memail, "M", "password")
        await set_grant(db, acc.id, member.id, proj.id, "viewer")
        sc = Scope(name=f"ar{s}", project_id=proj.id)
        db.add(sc)
        await db.flush()
        it = Item(scope_id=sc.id, project_id=proj.id, title="seed", type="feature",
                  status="backlog", origen="human")
        db.add(it)
        await db.commit()
        await db.refresh(it)
        pid, iid = proj.id, it.id
        break
    await client.post("/login", data={"email": memail, "password": "password"},
                      follow_redirects=False)
    await client.post("/ui/project/switch", data={"project_id": str(pid)}, follow_redirects=False)
    assert (await client.get("/backlog")).status_code == 200  # viewer can read
    w = await client.post(f"/ui/items/{iid}/transition", data={"status": "in-progress"})
    assert w.status_code == 403  # viewer cannot write
