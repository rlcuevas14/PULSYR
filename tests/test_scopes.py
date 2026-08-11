import uuid

import pytest
from httpx import AsyncClient


async def _admin_cookie(client: AsyncClient, suffix: str = "") -> dict:
    from app.auth.service import create_user
    from app.database import get_db
    email = f"scopeadmin{suffix}@test.cl"
    async for db in client.app.dependency_overrides[get_db]():
        await create_user(db, email, "Admin", "pass", "admin")
        break
    resp = await client.post(
        "/login",
        data={"email": email, "password": "pass"},
        follow_redirects=False,
    )
    return dict(resp.cookies)


@pytest.mark.asyncio
async def test_create_scope(client: AsyncClient):
    cookies = await _admin_cookie(client, suffix=uuid.uuid4().hex[:6])
    resp = await client.post(
        "/api/v1/scopes",
        json={"name": f"ia-chat-{uuid.uuid4().hex[:6]}", "description": "Chat IA", "color": "#3b82f6"},
        cookies=cookies,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "ia-chat" in data["name"]
    assert data["id"] is not None


@pytest.mark.asyncio
async def test_list_scopes(client: AsyncClient):
    cookies = await _admin_cookie(client, suffix=uuid.uuid4().hex[:6])
    scope_name = f"curriculo-{uuid.uuid4().hex[:6]}"
    await client.post("/api/v1/scopes", json={"name": scope_name}, cookies=cookies)
    resp = await client.get("/api/v1/scopes", cookies=cookies)
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()]
    assert scope_name in names


@pytest.mark.asyncio
async def test_patch_scope(client: AsyncClient):
    cookies = await _admin_cookie(client, suffix=uuid.uuid4().hex[:6])
    scope_name = f"temp-scope-{uuid.uuid4().hex[:6]}"
    r = await client.post("/api/v1/scopes", json={"name": scope_name}, cookies=cookies)
    scope_id = r.json()["id"]
    resp = await client.patch(
        f"/api/v1/scopes/{scope_id}",
        json={"archived": True},
        cookies=cookies,
    )
    assert resp.status_code == 200
    assert resp.json()["archived"] is True


@pytest.mark.asyncio
async def test_duplicate_scope_name_rejected(client: AsyncClient):
    cookies = await _admin_cookie(client, suffix=uuid.uuid4().hex[:6])
    scope_name = f"dup-scope-{uuid.uuid4().hex[:6]}"
    await client.post("/api/v1/scopes", json={"name": scope_name}, cookies=cookies)
    resp = await client.post("/api/v1/scopes", json={"name": scope_name}, cookies=cookies)
    assert resp.status_code == 409
