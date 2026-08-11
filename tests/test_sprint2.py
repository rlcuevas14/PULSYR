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
        user = await create_user(db, f"s2admin{suffix}@test.cl", "Admin", "pass", "admin")
        project = await db.scalar(select(Project).where(Project.account_id == user.account_id))
        scope = Scope(name=f"s2-{suffix}", project_id=project.id)
        db.add(scope)
        await db.commit()
        await db.refresh(scope)
        scope_id = str(scope.id)
        break
    resp = await client.post(
        "/login", data={"email": f"s2admin{suffix}@test.cl", "password": "pass"},
        follow_redirects=False,
    )
    return dict(resp.cookies), scope_id


async def _make(client, cookies, scope_id, title):
    r = await client.post(
        "/api/v1/items",
        json={"scope_id": scope_id, "title": title, "type": "feature"}, cookies=cookies,
    )
    return r.json()["id"]


@pytest.mark.asyncio
async def test_create_and_get_graph(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    a = await _make(client, cookies, scope_id, "alpha uno")
    b = await _make(client, cookies, scope_id, "beta dos")
    r = await client.post(
        "/api/v1/items/relationships",
        json={"source_id": a, "target_id": b, "relation": "blocks"}, cookies=cookies,
    )
    assert r.status_code == 201
    g = await client.get(f"/api/v1/items/{a}/graph", cookies=cookies)
    assert g.status_code == 200
    assert any(arc["relation"] == "blocks" for arc in g.json()["arcs"])


@pytest.mark.asyncio
async def test_self_loop_rejected(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    a = await _make(client, cookies, scope_id, "solo")
    r = await client.post(
        "/api/v1/items/relationships",
        json={"source_id": a, "target_id": a, "relation": "blocks"}, cookies=cookies,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_duplicate_is_idempotent(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    a = await _make(client, cookies, scope_id, "uno dup")
    b = await _make(client, cookies, scope_id, "dos dup")
    payload = {"source_id": a, "target_id": b, "relation": "blocks"}
    r1 = await client.post("/api/v1/items/relationships", json=payload, cookies=cookies)
    r2 = await client.post("/api/v1/items/relationships", json=payload, cookies=cookies)
    assert r1.status_code == 201
    assert r2.status_code == 201  # idempotente, no 500 por PK duplicada


@pytest.mark.asyncio
async def test_delete_relationship(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    a = await _make(client, cookies, scope_id, "borra a")
    b = await _make(client, cookies, scope_id, "borra b")
    await client.post(
        "/api/v1/items/relationships",
        json={"source_id": a, "target_id": b, "relation": "requires"}, cookies=cookies,
    )
    d = await client.delete(f"/api/v1/items/relationships/{a}/{b}/requires", cookies=cookies)
    assert d.status_code == 200
    assert d.json()["deleted"] is True


@pytest.mark.asyncio
async def test_ui_create_relationship_returns_partial(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    a = await _make(client, cookies, scope_id, "fuente unica xyz")
    await _make(client, cookies, scope_id, "destino unico abc")
    # crear vía UI resolviendo el target por texto
    r = await client.post(
        f"/ui/items/{a}/relationships",
        data={"relation": "blocks", "target_query": "destino unico abc"}, cookies=cookies,
    )
    assert r.status_code == 200
    assert "Relationships (graph)" in r.text  # default EN
    assert "destino unico abc" in r.text


@pytest.mark.asyncio
async def test_resolve_query_no_match(client: AsyncClient):
    cookies, scope_id = await _setup(client)
    a = await _make(client, cookies, scope_id, "fuente sola")
    r = await client.post(
        f"/ui/items/{a}/relationships",
        data={"relation": "blocks", "target_query": "noexistenadazzz"}, cookies=cookies,
    )
    assert r.status_code == 422
