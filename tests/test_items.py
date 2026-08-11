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
    scope_obj = None
    async for db in client.app.dependency_overrides[get_db]():
        user = await create_user(db, f"itemadmin{suffix}@test.cl", "Admin", "pass", "admin")
        project = await db.scalar(select(Project).where(Project.account_id == user.account_id))
        scope_obj = Scope(name=f"test-scope-{suffix}", project_id=project.id)
        db.add(scope_obj)
        await db.commit()
        await db.refresh(scope_obj)
        scope_id = str(scope_obj.id)
        break

    resp = await client.post(
        "/login",
        data={"email": f"itemadmin{suffix}@test.cl", "password": "pass"},
        follow_redirects=False,
    )
    return dict(resp.cookies), scope_id


@pytest.mark.asyncio
async def test_create_item(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    resp = await client.post(
        "/api/v1/items",
        json={
            "scope_id": scope_id,
            "title": "Bug de prueba",
            "type": "bug",
            "summary_md": "Descripción del bug.",
        },
        cookies=cookies,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "backlog"
    assert data["origen"] == "human"


@pytest.mark.asyncio
async def test_list_items_by_scope(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    await client.post(
        "/api/v1/items",
        json={"scope_id": scope_id, "title": "Feature X", "type": "feature"},
        cookies=cookies,
    )
    resp = await client.get(f"/api/v1/items?scope_id={scope_id}", cookies=cookies)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_patch_item_generates_event(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    r = await client.post(
        "/api/v1/items",
        json={"scope_id": scope_id, "title": "Patcheable", "type": "feature"},
        cookies=cookies,
    )
    item_id = r.json()["id"]
    resp = await client.patch(
        f"/api/v1/items/{item_id}",
        json={"status": "in-progress"},
        cookies=cookies,
    )
    assert resp.status_code == 200

    detail = await client.get(f"/api/v1/items/{item_id}", cookies=cookies)
    events = detail.json()["events"]
    assert any(e["action"] == "status_changed" for e in events)


@pytest.mark.asyncio
async def test_add_comment_is_append_only(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    r = await client.post(
        "/api/v1/items",
        json={"scope_id": scope_id, "title": "Con comentario", "type": "bug"},
        cookies=cookies,
    )
    item_id = r.json()["id"]
    resp = await client.post(
        f"/api/v1/items/{item_id}/comments",
        json={"body_md": "Primer comentario"},
        cookies=cookies,
    )
    assert resp.status_code == 201
    # No existe PATCH /comments — debe ser 405
    patch_resp = await client.patch(f"/api/v1/items/{item_id}/comments/some-id", json={})
    assert patch_resp.status_code == 405


@pytest.mark.asyncio
async def test_close_item(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    r = await client.post(
        "/api/v1/items",
        json={"scope_id": scope_id, "title": "Para cerrar", "type": "feature"},
        cookies=cookies,
    )
    item_id = r.json()["id"]
    resp = await client.post(
        f"/api/v1/items/{item_id}/close",
        json={"status": "done", "reason": "Completado en PR #99"},
        cookies=cookies,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"
    assert resp.json()["closed_at"] is not None
