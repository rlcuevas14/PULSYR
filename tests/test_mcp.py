import uuid

import pytest
from httpx import AsyncClient


async def _token(client: AsyncClient, scopes: str = "write") -> str:
    from app.accounts.service import create_account
    from app.auth.service import create_api_token
    from app.database import get_db
    from app.projects.service import create_project

    suffix = uuid.uuid4().hex[:8]
    async for db in client.app.dependency_overrides[get_db]():
        acc, owner = await create_account(db, f"acc-{suffix}", f"mcp{suffix}@test.cl", "MCP", "password")
        project = await create_project(
            db, name=f"proj-{suffix}", account_id=acc.id, preset="hybrid"
        )
        tok, raw = await create_api_token(db, f"mcp-{suffix}", scopes, owner.id)
        tok.project_id = project.id
        await db.commit()
        break
    return raw


def _hdr(raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


async def _rpc(client, raw, method, params=None, rpc_id=1):
    return await client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}},
        headers=_hdr(raw),
    )


async def _pid_of(client, raw):
    """Project id behind a token, so directly-seeded data lands in the token's project."""
    from sqlalchemy import select

    from app.auth.models import ApiToken
    from app.auth.service import _hash_token
    from app.database import get_db

    async for db in client.app.dependency_overrides[get_db]():
        return await db.scalar(
            select(ApiToken.project_id).where(ApiToken.token_hash == _hash_token(raw))
        )


@pytest.mark.asyncio
async def test_initialize_handshake(client: AsyncClient):
    raw = await _token(client)
    r = await _rpc(client, raw, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-03-26"
    assert "tools" in res["capabilities"]
    assert res["serverInfo"]["name"] == "pulsyr"


@pytest.mark.asyncio
async def test_no_token_401(client: AsyncClient):
    r = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_invalid_token_401(client: AsyncClient):
    r = await client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers=_hdr("token-falso-xyz"),
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_tools_list(client: AsyncClient):
    raw = await _token(client)
    r = await _rpc(client, raw, "tools/list")
    names = [t["name"] for t in r.json()["result"]["tools"]]
    assert "pulsyr_context" in names
    assert "pulsyr_create" in names
    assert "pulsyr_complete" in names


@pytest.mark.asyncio
async def test_notification_returns_202(client: AsyncClient):
    raw = await _token(client)
    r = await client.post(
        "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=_hdr(raw),
    )
    assert r.status_code == 202


@pytest.mark.asyncio
async def test_get_mcp_405(client: AsyncClient):
    r = await client.get("/mcp")
    assert r.status_code == 405


@pytest.mark.asyncio
async def test_pulsyr_crear_and_buscar(client: AsyncClient):
    raw = await _token(client, "write")
    r = await _rpc(client, raw, "tools/call", {
        "name": "pulsyr_create",
        "arguments": {"title": "Tarea MCP zzz", "type": "feature", "area_name": "mcp-scope"},
    })
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["isError"] is False
    # buscar la encuentra
    s = await _rpc(client, raw, "tools/call", {"name": "pulsyr_search", "arguments": {"q": "MCP zzz"}})
    import json
    found = json.loads(s.json()["result"]["content"][0]["text"])
    assert any("MCP zzz" in i["title"] for i in found)


@pytest.mark.asyncio
async def test_read_token_cannot_write(client: AsyncClient):
    raw = await _token(client, "read")
    r = await _rpc(client, raw, "tools/call", {
        "name": "pulsyr_create",
        "arguments": {"title": "no debería crearse", "type": "bug", "area_name": "x"},
    })
    assert r.status_code == 200
    assert r.json()["result"]["isError"] is True
    assert "write" in r.json()["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_pulsyr_completar_ambiguous_aborts(client: AsyncClient):
    from app.database import get_db
    from app.items.models import Item
    from app.scopes.models import Scope

    raw = await _token(client, "write")
    # pulsyr_create ahora es idempotente (dedup por title+scope abierto) → no se puede
    # forzar ambigüedad duplicando un título. La ambigüedad real ocurre cuando dos ítems
    # con títulos DISTINTOS empatan EXACTAMENTE en el rank FTS para la query (la palabra
    # «login» aparece una vez en cada título, mismo peso A → mismo ts_rank). Insertamos
    # directo en BD (como test_scope_tools) para controlar el dato sin depender de la tool.
    pid = await _pid_of(client, raw)
    sname = f"amb-{uuid.uuid4().hex[:6]}"
    async for db in client.app.dependency_overrides[get_db]():
        scope = Scope(name=sname, project_id=pid)
        db.add(scope)
        await db.flush()
        db.add(Item(scope_id=scope.id, title="login roto en movil",
                    type="bug", status="backlog", origen="human", project_id=pid))
        db.add(Item(scope_id=scope.id, title="login lento en desktop",
                    type="bug", status="backlog", origen="human", project_id=pid))
        await db.commit()
        break

    r = await _rpc(client, raw, "tools/call", {
        "name": "pulsyr_complete", "arguments": {"search_query": "login"},
    })
    assert r.json()["result"]["isError"] is True
    assert "ambig" in r.json()["result"]["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_pulsyr_contexto_runs(client: AsyncClient):
    raw = await _token(client)
    r = await _rpc(client, raw, "tools/call", {"name": "pulsyr_context", "arguments": {}})
    import json
    ctx = json.loads(r.json()["result"]["content"][0]["text"])
    assert "local" in ctx
    assert "neighborhood" in ctx
    assert ctx["semantic"] is None  # sin embeddings


@pytest.mark.asyncio
async def test_hilo_item_linking(client: AsyncClient):
    import json as _json

    from app.database import get_db
    from app.scopes.models import Scope

    raw = await _token(client, "write")
    pid = await _pid_of(client, raw)
    sname = f"billing-{uuid.uuid4().hex[:6]}"
    async for db in client.app.dependency_overrides[get_db]():
        db.add(Scope(name=sname, project_id=pid))
        await db.commit()
        break

    # crear hilo
    h = await _rpc(client, raw, "tools/call", {
        "name": "pulsyr_thread_create", "arguments": {"title": "Módulo Financiero", "area_name": sname}})
    hilo = _json.loads(h.json()["result"]["content"][0]["text"])
    hid = hilo["id"]

    # crear ítem colgado del hilo (thread_id en pulsyr_create)
    c = await _rpc(client, raw, "tools/call", {
        "name": "pulsyr_create",
        "arguments": {"title": "F0 núcleo cobranza", "type": "feature",
                      "area_name": sname, "thread_id": hid}})
    f0 = _json.loads(c.json()["result"]["content"][0]["text"])
    assert f0["thread_id"] == hid

    # crear ítem suelto y vincularlo después
    c2 = await _rpc(client, raw, "tools/call", {
        "name": "pulsyr_create",
        "arguments": {"title": "F1 pasarela chile", "type": "feature", "area_name": sname}})
    f1 = _json.loads(c2.json()["result"]["content"][0]["text"])
    assert f1["thread_id"] is None  # suelto: _item_brief SIEMPRE incluye thread_id (null si no)
    v = await _rpc(client, raw, "tools/call", {
        "name": "pulsyr_thread_link", "arguments": {"thread_id": hid, "item_id": f1["id"]}})
    linked = _json.loads(v.json()["result"]["content"][0]["text"])
    assert linked["thread_id"] == hid

    # pulsyr_thread detalle muestra los 2 ítems vinculados
    d = await _rpc(client, raw, "tools/call", {"name": "pulsyr_thread", "arguments": {"id": hid}})
    detail = _json.loads(d.json()["result"]["content"][0]["text"])
    titles = {i["title"] for i in detail["items"]}
    assert titles == {"F0 núcleo cobranza", "F1 pasarela chile"}


@pytest.mark.asyncio
async def test_method_not_found(client: AsyncClient):
    raw = await _token(client)
    r = await _rpc(client, raw, "no/existe")
    assert r.json()["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_scope_tools(client: AsyncClient):
    import json as _json

    from app.database import get_db
    from app.scopes.models import Scope

    raw = await _token(client, "write")
    pid = await _pid_of(client, raw)
    sname = f"curric-{uuid.uuid4().hex[:6]}"
    async for db in client.app.dependency_overrides[get_db]():
        db.add(Scope(name=sname, description="Currículo y OAs", project_id=pid))
        await db.commit()
        break

    # 1) pulsyr_areas lista con nombre + descripción
    sc = await _rpc(client, raw, "tools/call", {"name": "pulsyr_areas", "arguments": {}})
    scopes = _json.loads(sc.json()["result"]["content"][0]["text"])
    assert any(s["name"] == sname and s["description"] == "Currículo y OAs" for s in scopes)

    # 2) crear con variante de mayúsculas/espacios → matchea el existente, NO duplica
    cr = await _rpc(client, raw, "tools/call", {
        "name": "pulsyr_create",
        "arguments": {"title": "OA electivas", "type": "feature", "area_name": f"  {sname.upper()}  "},
    })
    created = _json.loads(cr.json()["result"]["content"][0]["text"])
    assert created["scope"] == sname  # devuelve el nombre del scope, no solo el id
    async for db in client.app.dependency_overrides[get_db]():
        n = await db.scalar(
            __import__("sqlalchemy").select(__import__("sqlalchemy").func.count())
            .select_from(Scope).where(__import__("sqlalchemy").func.lower(Scope.name) == sname.lower())
        )
        assert n == 1  # no se creó un duplicado por la variante de caso
        break

    # 3) mover a otro scope existente
    other = f"otro-{uuid.uuid4().hex[:6]}"
    async for db in client.app.dependency_overrides[get_db]():
        db.add(Scope(name=other, project_id=pid))
        await db.commit()
        break
    mv = await _rpc(client, raw, "tools/call", {
        "name": "pulsyr_move_area",
        "arguments": {"item_id": created["id"], "area_name": other},
    })
    moved = _json.loads(mv.json()["result"]["content"][0]["text"])
    assert moved["scope"] == other


@pytest.mark.asyncio
async def test_incident_tools_flow(client: AsyncClient, monkeypatch):
    import json as _json

    from app.database import get_db
    from app.webhooks import service as wservice
    from app.webhooks.models import SentryIssue

    raw = await _token(client, "write")
    pid = await _pid_of(client, raw)
    sid = f"inc-{uuid.uuid4().hex[:8]}"
    issue_id = None
    async for db in client.app.dependency_overrides[get_db]():
        issue = SentryIssue(sentry_issue_id=sid, project="python-fastapi",
                            title="KeyError en /api/x", level="error", status="new",
                            events_count=5, payload={"web_url": "https://sentry.io/i/1"},
                            project_id=pid)
        db.add(issue)
        await db.commit()
        await db.refresh(issue)
        issue_id = str(issue.id)
        break

    # 1) listar incidentes
    li = await _rpc(client, raw, "tools/call", {"name": "pulsyr_incidents", "arguments": {}})
    listed = _json.loads(li.json()["result"]["content"][0]["text"])
    assert any(i["id"] == issue_id for i in listed)

    # 2) detalle con stack trace (Sentry mockeado → no gasta nada)
    async def fake_detail(sentry_id, **kw):
        return {"title": "KeyError", "culprit": "app.api.x",
                "stacktrace": "KeyError: 'foo'\n  app/api/x.py:42 in handler"}

    monkeypatch.setattr(wservice, "fetch_issue_detail", fake_detail)
    det = await _rpc(client, raw, "tools/call", {"name": "pulsyr_incident", "arguments": {"id": issue_id}})
    detail = _json.loads(det.json()["result"]["content"][0]["text"])
    assert "x.py:42" in detail["stacktrace"]

    # 3) resolver (sin tocar Sentry)
    res = await _rpc(client, raw, "tools/call", {
        "name": "pulsyr_incident_resolve",
        "arguments": {"id": issue_id, "nota": "arreglado", "resolver_en_sentry": False},
    })
    out = _json.loads(res.json()["result"]["content"][0]["text"])
    assert out["status"] == "resolved"
    async for db in client.app.dependency_overrides[get_db]():
        issue = await db.get(SentryIssue, uuid.UUID(issue_id))
        assert issue.status == "resolved"
        break
