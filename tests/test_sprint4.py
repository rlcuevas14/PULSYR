import uuid

import pytest
from httpx import AsyncClient


async def _setup(client: AsyncClient):
    from sqlalchemy import select

    from app.auth.service import create_user
    from app.database import get_db
    from app.projects.models import Project
    from app.scopes.models import Scope

    suffix = uuid.uuid4().hex[:8]
    async for db in client.app.dependency_overrides[get_db]():
        user = await create_user(db, f"s4admin{suffix}@test.cl", "Admin", "pass", "admin")
        project = await db.scalar(select(Project).where(Project.account_id == user.account_id))
        from app.projects.modules import apply_preset

        await apply_preset(db, project.id, "product", user.email)
        scope = Scope(name=f"s4-{suffix}", project_id=project.id)
        db.add(scope)
        await db.commit()
        await db.refresh(scope)
        scope_name = scope.name
        break
    resp = await client.post(
        "/login", data={"email": f"s4admin{suffix}@test.cl", "password": "pass"},
        follow_redirects=False,
    )
    return dict(resp.cookies), scope_name


@pytest.mark.asyncio
async def test_thread_lifecycle(client: AsyncClient):
    cookies, scope_name = await _setup(client)
    r = await client.post(
        "/api/v1/threads",
        json={"scope_name": scope_name, "title": "Auth v2", "summary": "Refactor"}, cookies=cookies,
    )
    assert r.status_code == 201
    tid = r.json()["id"]
    assert r.json()["stage"] == "idea"

    # advance saving an artifact of the current stage: idea -> research (v0018 English values)
    a = await client.post(
        f"/api/v1/threads/{tid}/advance",
        json={"artifact_content": "Research notes."}, cookies=cookies,
    )
    assert a.status_code == 200
    assert a.json()["stage"] == "research"

    detail = await client.get(f"/api/v1/threads/{tid}", cookies=cookies)
    assert len(detail.json()["artifacts"]) == 1


@pytest.mark.asyncio
async def test_advance_to_hecho_blocked_by_open_items(client: AsyncClient):
    from app.database import get_db
    from app.items.models import Item
    from app.scopes.models import Scope

    cookies, scope_name = await _setup(client)
    r = await client.post(
        "/api/v1/threads", json={"scope_name": scope_name, "title": "Hilo con item"}, cookies=cookies,
    )
    tid = r.json()["id"]
    # llevar el hilo hasta review
    for _ in range(5):  # idea->research->stories->spec->in-development->review
        await client.post(f"/api/v1/threads/{tid}/advance", json={}, cookies=cookies)
    # linkear un ítem abierto
    async for db in client.app.dependency_overrides[get_db]():
        scope = (await db.execute(
            __import__("sqlalchemy").select(Scope).where(Scope.name == scope_name)
        )).scalar_one()
        it = Item(scope_id=scope.id, project_id=scope.project_id, title="abierto", type="feature",
                  status="in-progress", thread_id=uuid.UUID(tid))
        db.add(it)
        await db.commit()
        break
    # review -> hecho debe fallar (ítem abierto)
    bad = await client.post(f"/api/v1/threads/{tid}/advance", json={}, cookies=cookies)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_elaborate_without_key_graceful(client: AsyncClient):
    cookies, scope_name = await _setup(client)
    r = await client.post(
        "/api/v1/threads", json={"scope_name": scope_name, "title": "Para elaborar"}, cookies=cookies,
    )
    tid = r.json()["id"]
    e = await client.post(f"/api/v1/threads/{tid}/elaborate-stage", cookies=cookies)
    # sin ANTHROPIC_API_KEY degrada a 422 con mensaje claro
    assert e.status_code == 422


@pytest.mark.asyncio
async def test_hilos_pages_render(client: AsyncClient):
    cookies, scope_name = await _setup(client)
    r = await client.post(
        "/api/v1/threads", json={"scope_name": scope_name, "title": "Render hilo"}, cookies=cookies,
    )
    tid = r.json()["id"]
    page = await client.get("/threads", cookies=cookies)
    assert page.status_code == 200
    assert "Threads" in page.text  # default EN
    detail = await client.get(f"/threads/{tid}", cookies=cookies)
    assert detail.status_code == 200
    assert "Render hilo" in detail.text


@pytest.mark.asyncio
async def test_mcp_thread_tools(client: AsyncClient):
    from app.auth.service import create_api_token, create_user
    from app.database import get_db

    cookies, scope_name = await _setup(client)
    suffix = uuid.uuid4().hex[:8]
    async for db in client.app.dependency_overrides[get_db]():
        from app.projects.service import create_project
        user = await create_user(db, f"mcpt{suffix}@test.cl", "X", "password", "admin")
        project = await create_project(
            db, name=f"p-{suffix}", account_id=user.account_id, preset="product"
        )
        _t, raw = await create_api_token(db, f"t-{suffix}", "write", user.id)
        _t.project_id = project.id
        await db.commit()
        break

    async def rpc(method, params):
        return await client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers={"Authorization": f"Bearer {raw}"},
        )

    import json
    # Fresh area name: scopes.name is globally unique, so reusing _setup's scope name
    # (which lives in another project) would collide. The thread tool creates the area.
    r = await rpc("tools/call", {
        "name": "pulsyr_thread_create",
        "arguments": {"title": "Hilo MCP", "area_name": f"mcp-area-{suffix}"},
    })
    created = json.loads(r.json()["result"]["content"][0]["text"])
    assert created["stage"] == "idea"
    li = await rpc("tools/call", {"name": "pulsyr_thread_list", "arguments": {}})
    listed = json.loads(li.json()["result"]["content"][0]["text"])
    assert any(t["title"] == "Hilo MCP" for t in listed)
