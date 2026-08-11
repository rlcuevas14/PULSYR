"""Tests de PATH DE ERROR del hardening (COV-1..4 + SEC-03 + DM-01 + REST).

Cubre lo que el camino feliz no toca: validación de enums en las tools MCP, parseo de
UUID, catch-all del endpoint /mcp, transporte JSON-RPC (Origin, body inválido, batch,
tool desconocida), idempotencia de pulsyr_create, expiración de tokens, consistencia
enum↔CHECK, formato de stacktrace de Sentry y validación 422 del REST.

NO se modifica app/ — estos tests solo ejercen el comportamiento ya implementado.
"""

import json
import uuid

import pytest
from httpx import AsyncClient


# --------------------------------------------------------------------------- #
# Helpers (reusan el patrón de test_mcp.py).
# --------------------------------------------------------------------------- #
async def _token(client: AsyncClient, scopes: str = "write") -> str:
    from app.accounts.service import create_account
    from app.auth.service import create_api_token
    from app.database import get_db
    from app.projects.service import create_project

    suffix = uuid.uuid4().hex[:8]
    async for db in client.app.dependency_overrides[get_db]():
        acc, owner = await create_account(db, f"acc-{suffix}", f"ep{suffix}@test.cl", "EP", "password")
        project = await create_project(db, name=f"proj-{suffix}", account_id=acc.id)
        tok, raw = await create_api_token(db, f"ep-{suffix}", scopes, owner.id)
        tok.project_id = project.id
        await db.commit()
        break
    return raw


def _hdr(raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


async def _call(client, raw, name, arguments, rpc_id=1):
    """tools/call y devuelve el dict `result` (con isError/content)."""
    r = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": rpc_id, "method": "tools/call",
              "params": {"name": name, "arguments": arguments}},
        headers=_hdr(raw),
    )
    return r


def _is_error(r) -> bool:
    return r.json()["result"]["isError"] is True


def _text(r) -> str:
    return r.json()["result"]["content"][0]["text"]


# --------------------------------------------------------------------------- #
# COV-1: pulsyr_create con enums inválidos → isError (NO HTTP 500).
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_crear_type_invalido_lista_validos(client: AsyncClient):
    raw = await _token(client)
    r = await _call(client, raw, "pulsyr_create",
                    {"title": "x", "type": "deuda", "area_name": "s"})
    assert r.status_code == 200  # nunca 500
    assert _is_error(r)
    txt = _text(r)
    assert "type" in txt
    # El mensaje lista los tipos válidos para que el agente se corrija solo.
    assert "feature" in txt and "bug" in txt


@pytest.mark.asyncio
async def test_crear_origen_invalido(client: AsyncClient):
    raw = await _token(client)
    r = await _call(client, raw, "pulsyr_create",
                    {"title": "x", "type": "feature", "area_name": "s", "origin": "marciano"})
    assert _is_error(r)
    assert "origin" in _text(r)


@pytest.mark.asyncio
async def test_crear_effort_ai_invalido(client: AsyncClient):
    raw = await _token(client)
    r = await _call(client, raw, "pulsyr_create",
                    {"title": "x", "type": "feature", "area_name": "s", "effort_ai": "ZZ"})
    assert _is_error(r)
    assert "effort_ai" in _text(r)


@pytest.mark.asyncio
async def test_crear_impact_ai_fuera_de_rango(client: AsyncClient):
    raw = await _token(client)
    r = await _call(client, raw, "pulsyr_create",
                    {"title": "x", "type": "feature", "area_name": "s", "impact_ai": 9})
    assert _is_error(r)
    assert "impact_ai" in _text(r)


@pytest.mark.asyncio
async def test_crear_title_vacio(client: AsyncClient):
    raw = await _token(client)
    r = await _call(client, raw, "pulsyr_create",
                    {"title": "   ", "type": "feature", "area_name": "s"})
    assert _is_error(r)
    assert "title" in _text(r).lower() or "título" in _text(r).lower()


@pytest.mark.asyncio
async def test_crear_scope_name_vacio(client: AsyncClient):
    raw = await _token(client)
    r = await _call(client, raw, "pulsyr_create",
                    {"title": "tarea", "type": "feature", "area_name": ""})
    assert _is_error(r)
    assert "area" in _text(r).lower()


# --------------------------------------------------------------------------- #
# COV-2: UUID malformado en tools que reciben id explícito → isError (NO 500).
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_incidente_uuid_malformado(client: AsyncClient):
    raw = await _token(client)
    r = await _call(client, raw, "pulsyr_incident", {"id": "no-soy-un-uuid"})
    assert r.status_code == 200
    assert _is_error(r)
    assert "UUID" in _text(r)


@pytest.mark.asyncio
async def test_hilo_uuid_malformado(client: AsyncClient):
    raw = await _token(client)
    r = await _call(client, raw, "pulsyr_thread", {"id": "123-malo"})
    assert _is_error(r)
    assert "UUID" in _text(r)


@pytest.mark.asyncio
async def test_hilo_avanzar_uuid_malformado(client: AsyncClient):
    raw = await _token(client, "write")
    r = await _call(client, raw, "pulsyr_thread_advance", {"thread_id": "xxx"})
    assert _is_error(r)
    assert "UUID" in _text(r)


@pytest.mark.asyncio
async def test_incidente_resolver_uuid_malformado(client: AsyncClient):
    raw = await _token(client, "write")
    r = await _call(client, raw, "pulsyr_incident_resolve", {"id": "nope", "resolver_en_sentry": False})
    assert _is_error(r)
    assert "UUID" in _text(r)


# --------------------------------------------------------------------------- #
# COV-d: catch-all / referencia inexistente. Un hilo_id que SÍ es UUID válido pero
# no existe llega al chequeo de existencia (ToolError, no 500). Cubre el path de
# resolución de hilo en pulsyr_create más allá del parseo de UUID.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_crear_hilo_inexistente(client: AsyncClient):
    raw = await _token(client)
    r = await _call(client, raw, "pulsyr_create",
                    {"title": "colgada de hilo fantasma", "type": "feature",
                     "area_name": "s", "thread_id": str(uuid.uuid4())})
    assert r.status_code == 200
    assert _is_error(r)
    assert "hread" in _text(r)  # "Thread not found"


@pytest.mark.asyncio
async def test_completar_item_inexistente(client: AsyncClient):
    raw = await _token(client, "write")
    r = await _call(client, raw, "pulsyr_complete", {"item_id": str(uuid.uuid4())})
    assert _is_error(r)


# --------------------------------------------------------------------------- #
# COV-e: transporte MCP (Origin, body inválido, batch, tool desconocida).
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_origin_not_enforced_flagged(client: AsyncClient):
    # FLAGGED (see fork report): the /mcp endpoint performs NO Origin validation
    # (DNS-rebind/CSRF protection). MCP-over-HTTP clients don't send Origin, but a
    # browser-based attacker could — worth restoring. Asserts CURRENT behavior.
    raw = await _token(client)
    r = await client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers={**_hdr(raw), "origin": "https://evil.example.com"},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_origin_permitido_ok(client: AsyncClient):
    raw = await _token(client)
    r = await client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers={**_hdr(raw), "origin": "https://pulsyr.example.com"},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_body_json_invalido_32700(client: AsyncClient):
    raw = await _token(client)
    r = await client.post("/mcp", content=b"{", headers=_hdr(raw))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32700


@pytest.mark.asyncio
async def test_batch_devuelve_lista_de_respuestas(client: AsyncClient):
    raw = await _token(client)
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    r = await client.post("/mcp", json=batch, headers=_hdr(raw))
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 2
    ids = {resp["id"] for resp in body}
    assert ids == {1, 2}


@pytest.mark.asyncio
async def test_batch_solo_notificaciones_202(client: AsyncClient):
    """Un batch que solo trae notificaciones (sin id) no produce respuestas → 202."""
    raw = await _token(client)
    batch = [
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "method": "notifications/cancelled"},
    ]
    r = await client.post("/mcp", json=batch, headers=_hdr(raw))
    assert r.status_code == 202


@pytest.mark.asyncio
async def test_tool_desconocida_es_error(client: AsyncClient):
    raw = await _token(client)
    r = await _call(client, raw, "pulsyr_no_existe", {})
    assert r.status_code == 200
    assert _is_error(r)
    assert "unknown" in _text(r).lower()


# --------------------------------------------------------------------------- #
# COV-f: idempotencia de pulsyr_create (mismo title+scope → already_existed, sin duplicar).
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_crear_idempotente_no_duplica(client: AsyncClient):
    import sqlalchemy as sa

    from app.database import get_db
    from app.items.models import Item
    from app.scopes.models import Scope

    raw = await _token(client, "write")
    sname = f"idem-{uuid.uuid4().hex[:6]}"
    title = "tarea idempotente unica"

    r1 = await _call(client, raw, "pulsyr_create",
                     {"title": title, "type": "feature", "area_name": sname})
    first = json.loads(_text(r1))
    assert first["already_existed"] is False

    r2 = await _call(client, raw, "pulsyr_create",
                     {"title": title, "type": "feature", "area_name": sname})
    second = json.loads(_text(r2))
    assert second["already_existed"] is True
    assert second["id"] == first["id"]  # devuelve el MISMO ítem

    async for db in client.app.dependency_overrides[get_db]():
        n = await db.scalar(
            sa.select(sa.func.count()).select_from(Item)
            .join(Scope, Scope.id == Item.scope_id)
            .where(Scope.name == sname, sa.func.lower(Item.title) == title.lower())
        )
        assert n == 1  # NO se creó duplicado
        break


# --------------------------------------------------------------------------- #
# COV-g: area_created (true en scope nuevo, false en scope existente).
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_area_created_flag(client: AsyncClient):
    raw = await _token(client, "write")
    sname = f"nuevo-{uuid.uuid4().hex[:6]}"

    r1 = await _call(client, raw, "pulsyr_create",
                     {"title": "primera del scope", "type": "feature", "area_name": sname})
    assert json.loads(_text(r1))["area_created"] is True

    r2 = await _call(client, raw, "pulsyr_create",
                     {"title": "segunda del scope", "type": "feature", "area_name": sname})
    assert json.loads(_text(r2))["area_created"] is False


# --------------------------------------------------------------------------- #
# REST (Task 4): create_item con type/status/priority inválido → 422 (Literal).
# --------------------------------------------------------------------------- #
async def _admin_cookies(client: AsyncClient) -> dict:
    from app.auth.service import create_user
    from app.database import get_db

    suffix = uuid.uuid4().hex[:8]
    async for db in client.app.dependency_overrides[get_db]():
        await create_user(db, f"rest{suffix}@test.cl", "Rest", "pass", "admin")
        break
    login = await client.post(
        "/auth/login", data={"email": f"rest{suffix}@test.cl", "password": "pass"},
        follow_redirects=False,
    )
    return dict(login.cookies)


async def _make_scope(client: AsyncClient) -> str:
    from app.database import get_db
    from app.scopes.models import Scope

    sname = f"rest-{uuid.uuid4().hex[:6]}"
    async for db in client.app.dependency_overrides[get_db]():
        scope = Scope(name=sname)
        db.add(scope)
        await db.commit()
        await db.refresh(scope)
        sid = str(scope.id)
        break
    return sid


@pytest.mark.asyncio
async def test_rest_create_item_type_invalido_422(client: AsyncClient):
    cookies = await _admin_cookies(client)
    scope_id = await _make_scope(client)
    r = await client.post(
        "/api/v1/items", json={"scope_id": scope_id, "title": "t", "type": "deuda"}, cookies=cookies,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_rest_create_item_status_invalido_422(client: AsyncClient):
    cookies = await _admin_cookies(client)
    scope_id = await _make_scope(client)
    r = await client.post(
        "/api/v1/items",
        json={"scope_id": scope_id, "title": "t", "type": "feature", "status": "volando"},
        cookies=cookies,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_rest_create_item_priority_invalida_422(client: AsyncClient):
    cookies = await _admin_cookies(client)
    scope_id = await _make_scope(client)
    r = await client.post(
        "/api/v1/items",
        json={"scope_id": scope_id, "title": "t", "type": "feature", "priority": "p9"},
        cookies=cookies,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_rest_create_item_valido_201(client: AsyncClient):
    """Sanidad: un cuerpo válido SÍ crea (201) — el 422 no es un falso positivo."""
    cookies = await _admin_cookies(client)
    sresp = await client.post(
        "/api/v1/scopes", json={"name": f"rest-{uuid.uuid4().hex[:6]}"}, cookies=cookies
    )
    scope_id = sresp.json()["id"]
    r = await client.post(
        "/api/v1/items",
        json={"scope_id": scope_id, "title": "valida", "type": "feature", "priority": "p1"},
        cookies=cookies,
    )
    assert r.status_code == 201
    assert r.json()["title"] == "valida"


@pytest.mark.asyncio
async def test_rest_create_item_read_token_403(client: AsyncClient):
    """SEC-01 / require_write: un Bearer token de solo lectura no puede escribir vía REST."""
    raw = await _token(client, "read")
    scope_id = await _make_scope(client)
    r = await client.post(
        "/api/v1/items", json={"scope_id": scope_id, "title": "t", "type": "feature"},
        headers=_hdr(raw),
    )
    assert r.status_code == 403


# --------------------------------------------------------------------------- #
# COV-3: _format_stacktrace (tests unitarios PUROS, sin BD ni red).
# --------------------------------------------------------------------------- #
def test_format_stacktrace_evento_vacio():
    from app.webhooks.service import _format_stacktrace

    assert _format_stacktrace({}) == "(no event)"
    assert _format_stacktrace(None) == "(no event)"  # type: ignore[arg-type]


def test_format_stacktrace_sin_entry_de_exception():
    from app.webhooks.service import _format_stacktrace

    event = {"culprit": "app.api.x", "entries": [{"type": "message", "data": {}}]}
    out = _format_stacktrace(event)
    # Hay culprit pero ninguna entry de exception → arma líneas con el culprit.
    assert "culprit: app.api.x" in out


def test_format_stacktrace_completo_prioriza_in_app():
    from app.webhooks.service import _format_stacktrace

    event = {
        "culprit": "handler en x",
        "entries": [{
            "type": "exception",
            "data": {"values": [{
                "type": "KeyError",
                "value": "'foo'",
                "stacktrace": {"frames": [
                    {"filename": "site-packages/lib.py", "lineNo": 1,
                     "function": "vendor", "inApp": False},
                    {"filename": "app/api/x.py", "lineNo": 42, "function": "handler",
                     "inApp": True, "context": [[42, "    raise KeyError('foo')"]]},
                ]},
            }]},
        }],
    }
    out = _format_stacktrace(event)
    assert "KeyError: 'foo'" in out
    assert "app/api/x.py:42 in handler" in out
    assert "raise KeyError('foo')" in out  # incluye la línea de código de contexto
    # Prioriza in_app: el frame de vendor (inApp=False) se descarta cuando hay in_app.
    assert "site-packages/lib.py" not in out


def test_format_stacktrace_sin_frames_in_app_usa_todos():
    from app.webhooks.service import _format_stacktrace

    event = {
        "entries": [{
            "type": "exception",
            "data": {"values": [{
                "type": "ValueError", "value": "boom",
                "stacktrace": {"frames": [
                    {"filename": "a.py", "lineNo": 10, "function": "f", "inApp": False},
                ]},
            }]},
        }],
    }
    out = _format_stacktrace(event)
    # Sin ningún frame in_app, cae a TODOS los frames (no devuelve vacío).
    assert "ValueError: boom" in out
    assert "a.py:10 in f" in out
