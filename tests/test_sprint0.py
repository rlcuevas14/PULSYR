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
        user = await create_user(db, f"s0admin{suffix}@test.cl", "Admin", "pass", "admin")
        project = await db.scalar(select(Project).where(Project.account_id == user.account_id))
        scope = Scope(name=f"s0-{suffix}", project_id=project.id)
        db.add(scope)
        await db.commit()
        await db.refresh(scope)
        scope_id = str(scope.id)
        break
    resp = await client.post(
        "/login", data={"email": f"s0admin{suffix}@test.cl", "password": "pass"},
        follow_redirects=False,
    )
    return dict(resp.cookies), scope_id


async def _make(client, cookies, scope_id, **kw):
    body = {"scope_id": scope_id, "title": "T", "type": "feature"}
    body.update(kw)
    r = await client.post("/api/v1/items", json=body, cookies=cookies)
    return r.json()["id"]


@pytest.mark.asyncio
async def test_invalid_transition_rejected(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    item_id = await _make(client, cookies, scope_id)  # backlog
    # backlog -> in-review NO es válido.
    r = await client.patch(f"/api/v1/items/{item_id}", json={"status": "in-review"}, cookies=cookies)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_terminal_via_patch_rejected(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    item_id = await _make(client, cookies, scope_id)
    # No se puede pasar a 'done' por PATCH (debe usar /close).
    r = await client.patch(f"/api/v1/items/{item_id}", json={"status": "done"}, cookies=cookies)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_valid_transition_ok(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    item_id = await _make(client, cookies, scope_id)
    r = await client.patch(f"/api/v1/items/{item_id}", json={"status": "in-progress"}, cookies=cookies)
    assert r.status_code == 200
    assert r.json()["status"] == "in-progress"


@pytest.mark.asyncio
async def test_priority_sets_declared(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    item_id = await _make(client, cookies, scope_id)
    await client.patch(f"/api/v1/items/{item_id}", json={"priority": "p1"}, cookies=cookies)
    detail = await client.get(f"/api/v1/items/{item_id}", cookies=cookies)
    assert detail.json()["priority"] == "p1"
    assert detail.json()["priority_declared"] == "p1"


@pytest.mark.asyncio
async def test_close_reports_unblocked(client: AsyncClient):
    from app.database import get_db
    from app.items.models import ItemRelationship

    cookies, scope_id = await _setup(client)
    blocker = await _make(client, cookies, scope_id, title="blocker")
    blocked = await _make(client, cookies, scope_id, title="blocked")
    # blocker bloquea a blocked (arco insertado vía DB; el endpoint REST llega en Sprint 2)
    async for db in client.app.dependency_overrides[get_db]():
        db.add(ItemRelationship(
            source_id=uuid.UUID(blocker), target_id=uuid.UUID(blocked), relation="blocks"
        ))
        await db.commit()
        break
    # cerrar blocker -> reporta blocked como desbloqueado
    r = await client.post(
        f"/api/v1/items/{blocker}/close", json={"status": "done", "reason": "done"}, cookies=cookies,
    )
    assert r.status_code == 200
    unblocked_ids = [u["id"] for u in r.json()["unblocked"]]
    assert blocked in unblocked_ids


@pytest.mark.asyncio
async def test_reopen(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    item_id = await _make(client, cookies, scope_id)
    await client.post(f"/api/v1/items/{item_id}/close", json={"status": "done"}, cookies=cookies)
    r = await client.post(f"/api/v1/items/{item_id}/reopen", cookies=cookies)
    assert r.status_code == 200
    assert r.json()["status"] == "backlog"


@pytest.mark.asyncio
async def test_ui_transition_endpoint(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    item_id = await _make(client, cookies, scope_id)
    r = await client.post(
        f"/ui/items/{item_id}/transition", data={"status": "in-progress"}, cookies=cookies,
    )
    assert r.status_code == 204
    assert r.headers.get("HX-Refresh") == "true"


@pytest.mark.asyncio
async def test_ui_create_item(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    r = await client.post(
        "/ui/items/create",
        data={"title": "Creado UI", "scope_id": scope_id, "type": "bug"},
        cookies=cookies, follow_redirects=False,
    )
    assert r.status_code == 303


@pytest.mark.asyncio
async def test_prioridad_page_renders(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    await _make(client, cookies, scope_id, impact_ai=5)
    r = await client.get("/priority", cookies=cookies)
    assert r.status_code == 200
    assert "Priority" in r.text  # default EN


@pytest.mark.asyncio
async def test_admin_generate_service_token(client: AsyncClient):
    """Admin tokens carry no project_id, so the result must NOT offer the MCP
    connect snippet (2026-07-16 audit, A2) — MCP tokens come from project settings."""
    cookies, _ = await _setup(client)  # _setup creates an admin and logs in
    r = await client.post(
        "/ui/admin/tokens", data={"name": "claude-code", "scopes": "write"}, cookies=cookies,
    )
    assert r.status_code == 200
    assert "claude mcp add" not in r.text
    assert "claude-code" in r.text  # the created token row is rendered


@pytest.mark.asyncio
async def test_item_detail_and_backlog_render(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    item_id = await _make(client, cookies, scope_id)
    detail = await client.get(f"/items/{item_id}", cookies=cookies)
    assert detail.status_code == 200
    assert "Relationships (graph)" in detail.text  # default EN
    backlog = await client.get("/backlog", cookies=cookies)
    assert backlog.status_code == 200
    dash = await client.get("/", cookies=cookies)
    assert dash.status_code == 200
