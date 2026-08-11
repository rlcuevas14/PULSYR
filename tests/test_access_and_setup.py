"""Coverage for the first-run setup wizard and the project-resolver edge cases (access.py)."""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.database import get_db


@pytest.mark.asyncio
async def test_setup_wizard_first_run(client: AsyncClient):
    # Empty the tenant tables so _no_users() is true and the wizard runs.
    async for db in client.app.dependency_overrides[get_db]():
        await db.execute(text("TRUNCATE accounts RESTART IDENTITY CASCADE"))
        await db.commit()
        break
    r = await client.post(
        "/setup",
        data={"name": "Founder", "email": "founder@t.cl", "password": "password1",
              "project_name": "First Project"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "/projects/" in r.headers["location"]
    # the owner is a superadmin and can reach the accounts admin
    assert (await client.get("/admin/accounts")).status_code == 200
    # with a user now present, /setup bounces to /
    again = await client.get("/setup", follow_redirects=False)
    assert again.status_code == 303 and again.headers["location"] == "/"


@pytest.mark.asyncio
async def test_token_without_project_rejected_on_rest(client: AsyncClient):
    from app.accounts.service import create_account
    from app.auth.service import create_api_token

    s = uuid.uuid4().hex[:8]
    async for db in client.app.dependency_overrides[get_db]():
        acc, owner = await create_account(db, f"a{s}", f"np{s}@t.cl", "T", "password")
        _tok, raw = await create_api_token(db, "np", "read", owner.id)  # project_id stays None
        await db.commit()
        break
    r = await client.get("/api/v1/scopes", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 400  # "Token has no project assigned"


@pytest.mark.asyncio
async def test_session_user_no_selection_falls_back(client: AsyncClient):
    """A logged-in user with no explicit project selection still resolves to their project."""
    from app.auth.service import create_user

    email = f"fb{uuid.uuid4().hex[:6]}@t.cl"
    async for db in client.app.dependency_overrides[get_db]():
        await create_user(db, email, "FB", "password", "admin")  # auto-account + Default project
        break
    await client.post("/login", data={"email": email, "password": "password"},
                      follow_redirects=False)
    # no /ui/project/switch performed → resolver falls back to the user's first project
    r = await client.get("/api/v1/scopes")
    assert r.status_code == 200
