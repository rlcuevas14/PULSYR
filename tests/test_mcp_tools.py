"""Broad MCP coverage: every tool lifecycle + prompts + resources + batch + error branches."""
import json
import uuid

import pytest
from httpx import AsyncClient


async def _setup(client: AsyncClient, scopes: str = "write"):
    from app.accounts.service import create_account
    from app.auth.service import create_api_token
    from app.database import get_db
    from app.projects.service import create_project

    s = uuid.uuid4().hex[:8]
    async for db in client.app.dependency_overrides[get_db]():
        acc, owner = await create_account(db, f"a{s}", f"t{s}@t.cl", "T", "password")
        proj = await create_project(db, name=f"p{s}", account_id=acc.id)
        tok, raw = await create_api_token(db, f"tok{s}", scopes, owner.id)
        tok.project_id = proj.id
        await db.commit()
        return raw, proj.id


def _hdr(raw):
    return {"Authorization": f"Bearer {raw}"}


async def _rpc(client, raw, method, params=None, rpc_id=1):
    r = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}},
        headers=_hdr(raw),
    )
    return r


async def _call(client, raw, name, args):
    r = await _rpc(client, raw, "tools/call", {"name": name, "arguments": args})
    return r.json()["result"]


def _text(result):
    return result["content"][0]["text"]


def _data(result):
    return json.loads(_text(result))


@pytest.mark.asyncio
async def test_full_tool_lifecycle(client: AsyncClient):
    raw, _pid = await _setup(client)

    # context (both with and without work_description)
    ctx = _data(await _call(client, raw, "pulsyr_context", {}))
    assert "local" in ctx and ctx["semantic"] is None
    _data(await _call(client, raw, "pulsyr_context", {"work_description": "auth refactor"}))

    # create two items
    a = _data(await _call(client, raw, "pulsyr_create",
                          {"title": "Login flow", "type": "feature", "area_name": "auth"}))
    b = _data(await _call(client, raw, "pulsyr_create",
                          {"title": "Token refresh", "type": "bug", "area_name": "auth"}))
    assert a["scope"] == "auth"

    # search finds it
    found = _data(await _call(client, raw, "pulsyr_search", {"q": "Login flow"}))
    assert any("Login flow" in i["title"] for i in found)

    # list with each order
    for order in ("impact", "priority", "topological", "recent"):
        listed = _data(await _call(client, raw, "pulsyr_list", {"order": order}))
        assert isinstance(listed, list)

    # areas
    areas = _data(await _call(client, raw, "pulsyr_areas", {}))
    assert any(s["name"] == "auth" for s in areas)

    # advance (valid) + invalid status (isError)
    adv = _data(await _call(client, raw, "pulsyr_advance",
                            {"item_id": a["id"], "to_status": "in-progress"}))
    assert adv["status"] == "in-progress"
    bad = await _call(client, raw, "pulsyr_advance", {"item_id": a["id"], "to_status": "bogus"})
    assert bad["isError"] is True

    # link the two (returns a plain-text confirmation, not JSON)
    link_res = await _call(client, raw, "pulsyr_link",
                           {"source_id": a["id"], "target_id": b["id"], "relation": "blocks"})
    assert link_res["isError"] is False

    # move_area (target area must exist first — create an item there)
    _data(await _call(client, raw, "pulsyr_create",
                      {"title": "Seed billing", "type": "feature", "area_name": "billing"}))
    moved = _data(await _call(client, raw, "pulsyr_move_area",
                              {"item_id": b["id"], "area_name": "billing"}))
    assert moved["scope"] == "billing"

    # complete with note + commit (returns a plain-text summary)
    done = await _call(client, raw, "pulsyr_complete",
                       {"item_id": a["id"], "note": "shipped", "commit_sha": "abc1234"})
    assert done["isError"] is False


@pytest.mark.asyncio
async def test_pulsyr_context_rich(client: AsyncClient):
    raw, pid = await _setup(client)
    from app.database import get_db
    from app.items.models import Item
    from app.scopes.models import Scope
    from app.threads.service import create_thread
    from app.webhooks.models import SentryIssue

    async for db in client.app.dependency_overrides[get_db]():
        sc = Scope(name="core", project_id=pid)
        db.add(sc)
        await db.flush()
        db.add(Item(scope_id=sc.id, project_id=pid, title="Quick win", type="feature",
                    status="backlog", origen="human", priority="p0", impact_ai=5, effort_ai="XS"))
        db.add(Item(scope_id=sc.id, project_id=pid, title="Blocked item", type="bug",
                    status="blocked", origen="human"))
        db.add(SentryIssue(sentry_issue_id=f"c{uuid.uuid4().hex[:8]}", project="x", title="E",
                           level="error", status="new", events_count=1, payload={}, project_id=pid))
        t = await create_thread(db, "core", "Active thread", None, project_id=pid)
        t.stage = "in-development"
        await db.commit()
        break
    ctx = _data(await _call(client, raw, "pulsyr_context",
                            {"area": "core", "work_description": "refactor auth"}))
    assert "neighborhood" in ctx and ctx["semantic"] is None
    assert any(t["title"] == "Active thread" for t in ctx["local"]["active_threads"])
    assert any(i["title"] == "Blocked item" for i in ctx["local"]["blockers"])


@pytest.mark.asyncio
async def test_thread_and_incident_tools(client: AsyncClient):
    raw, pid = await _setup(client)
    t = _data(await _call(client, raw, "pulsyr_thread_create",
                          {"title": "Payments", "area_name": "billing"}))
    _data(await _call(client, raw, "pulsyr_thread_advance",
                      {"thread_id": t["id"], "artifact_content": "research"}))
    item = _data(await _call(client, raw, "pulsyr_create",
                             {"title": "Gateway", "type": "feature", "area_name": "billing"}))
    linked = _data(await _call(client, raw, "pulsyr_thread_link",
                               {"thread_id": t["id"], "item_id": item["id"]}))
    assert linked["thread_id"] == t["id"]
    tl = _data(await _call(client, raw, "pulsyr_thread_list", {}))
    assert any(x["id"] == t["id"] for x in tl)
    detail = _data(await _call(client, raw, "pulsyr_thread", {"id": t["id"]}))
    assert detail["title"] == "Payments"

    # incidents: seed a SentryIssue in this project
    from app.database import get_db
    from app.webhooks.models import SentryIssue
    async for db in client.app.dependency_overrides[get_db]():
        issue = SentryIssue(sentry_issue_id=f"i{uuid.uuid4().hex[:8]}", project="api",
                            title="Boom", level="error", status="new", events_count=2,
                            payload={"web_url": "x"}, project_id=pid)
        db.add(issue)
        await db.commit()
        await db.refresh(issue)
        iid = str(issue.id)
        break
    incs = _data(await _call(client, raw, "pulsyr_incidents", {}))
    assert any(i["id"] == iid for i in incs)
    res = await _call(client, raw, "pulsyr_incident_resolve",
                      {"id": iid, "resolve_in_sentry": False})
    assert res["isError"] is False


@pytest.mark.asyncio
async def test_prompts(client: AsyncClient):
    raw, _pid = await _setup(client)
    lst = (await _rpc(client, raw, "prompts/list")).json()["result"]
    assert any(p["name"] == "briefing" for p in lst["prompts"])
    brief = (await _rpc(client, raw, "prompts/get", {"name": "briefing"})).json()["result"]
    assert "session context" in brief["messages"][0]["content"]["text"]
    dec = (await _rpc(client, raw, "prompts/get",
                      {"name": "decision", "arguments": {"topic": "auth"}})).json()
    assert "decisions" in dec["result"]["messages"][0]["content"]["text"].lower()
    unknown = (await _rpc(client, raw, "prompts/get", {"name": "nope"})).json()
    assert unknown["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_resources(client: AsyncClient):
    raw, _pid = await _setup(client)
    _data(await _call(client, raw, "pulsyr_create",
                      {"title": "Res item", "type": "feature", "area_name": "core"}))
    tmpl = (await _rpc(client, raw, "resources/templates/list")).json()["result"]
    assert "resourceTemplates" in tmpl
    assert (await _rpc(client, raw, "resources/list")).json()["result"] == {"resources": []}
    area = (await _rpc(client, raw, "resources/read", {"uri": "pulsyr://area/core"})).json()
    body = json.loads(area["result"]["contents"][0]["text"])
    assert body["area"] == "core"
    bad = (await _rpc(client, raw, "resources/read", {"uri": "pulsyr://nope/x"})).json()
    assert bad["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_batch_and_errors(client: AsyncClient):
    raw, _pid = await _setup(client)
    # batch: two calls in one request
    batch = await client.post("/mcp", json=[
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "pulsyr_areas_not_a_method"},
    ], headers=_hdr(raw))
    assert isinstance(batch.json(), list) and len(batch.json()) == 2

    # unknown tool
    unk = await _call(client, raw, "pulsyr_does_not_exist", {})
    assert unk["isError"] is True and "Unknown tool" in _text(unk)

    # missing required argument (KeyError path) — create without title
    miss = await _call(client, raw, "pulsyr_create", {"type": "feature", "area_name": "x"})
    assert miss["isError"] is True

    # read-only token cannot write
    raw_ro, _ = await _setup(client, scopes="read")
    ro = await _call(client, raw_ro, "pulsyr_create",
                     {"title": "no", "type": "bug", "area_name": "x"})
    assert ro["isError"] is True and "write" in _text(ro)


@pytest.mark.asyncio
async def test_mcp_auth_failures(client: AsyncClient):
    no_tok = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert no_tok.status_code == 401
    bad_tok = await client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": "Bearer nope"},
    )
    assert bad_tok.status_code == 401
    get_mcp = await client.get("/mcp")
    assert get_mcp.status_code in (200, 405)
