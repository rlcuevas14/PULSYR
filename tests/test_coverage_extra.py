"""Coverage for MCP-tool error/filter branches and a few REST edges — all real behavior."""
import json
import uuid

import pytest


async def _token_pid(client, scopes: str = "write"):
    from app.accounts.service import create_account
    from app.auth.service import create_api_token
    from app.database import get_db
    from app.projects.service import create_project

    s = uuid.uuid4().hex[:8]
    async for db in client.app.dependency_overrides[get_db]():
        acc, owner = await create_account(db, f"a{s}", f"o{s}@t.cl", "O", "passw0rd")
        proj = await create_project(db, name=f"p{s}", account_id=acc.id)
        tok, raw = await create_api_token(db, f"t{s}", scopes, owner.id)
        tok.project_id = proj.id
        await db.commit()
        break
    return raw, proj.id


async def _call(client, raw, name, args):
    r = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": name, "arguments": args}},
        headers={"Authorization": f"Bearer {raw}"},
    )
    return r.json()["result"]


def _err(res) -> bool:
    return res["isError"] is True


def _text(res) -> str:
    return res["content"][0]["text"]


def _data(res):
    return json.loads(res["content"][0]["text"])


async def _make_item(client, raw, title="Task one", type="feature", area="backend"):
    res = await _call(client, raw, "pulsyr_create",
                      {"title": title, "type": type, "area_name": area})
    return _data(res)


# ---------- pulsyr_list orders + pulsyr_search filters ----------

@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["impact", "priority", "topological", "recent"])
async def test_list_orders(client, order):
    raw, _ = await _token_pid(client)
    await _make_item(client, raw, title=f"item-{order}", area="ops")
    res = await _call(client, raw, "pulsyr_list", {"order": order, "limit": 50})
    assert not _err(res)
    assert isinstance(_data(res), list)


@pytest.mark.asyncio
async def test_search_area_and_type_filters(client):
    raw, _ = await _token_pid(client)
    await _make_item(client, raw, title="login bug alpha", type="bug", area="auth")
    await _make_item(client, raw, title="login feature beta", type="feature", area="ui")
    only_bug = _data(await _call(client, raw, "pulsyr_search", {"q": "login", "type": "bug"}))
    assert all(r["type"] == "bug" for r in only_bug)
    only_auth = _data(await _call(client, raw, "pulsyr_search", {"q": "login", "area": "auth"}))
    assert all((r["area"] or "").lower() == "auth" for r in only_auth)


# ---------- error branches ----------

@pytest.mark.asyncio
async def test_move_area_to_nonexistent_area_errors(client):
    raw, _ = await _token_pid(client)
    it = await _make_item(client, raw)
    res = await _call(client, raw, "pulsyr_move_area",
                      {"item_id": it["id"], "area_name": "ghost-area"})
    assert _err(res)


@pytest.mark.asyncio
async def test_move_area_missing_area_name_errors(client):
    raw, _ = await _token_pid(client)
    it = await _make_item(client, raw)
    res = await _call(client, raw, "pulsyr_move_area", {"item_id": it["id"]})
    assert _err(res) and "area_name" in _text(res)


@pytest.mark.asyncio
async def test_advance_missing_to_status_errors(client):
    raw, _ = await _token_pid(client)
    it = await _make_item(client, raw)
    res = await _call(client, raw, "pulsyr_advance", {"item_id": it["id"]})
    assert _err(res) and "to_status" in _text(res)


@pytest.mark.asyncio
async def test_advance_without_item_ref_errors(client):
    raw, _ = await _token_pid(client)
    res = await _call(client, raw, "pulsyr_advance", {"to_status": "spec"})
    assert _err(res)


@pytest.mark.asyncio
async def test_advance_invalid_transition_errors(client):
    raw, _ = await _token_pid(client)
    it = await _make_item(client, raw)
    res = await _call(client, raw, "pulsyr_advance", {"item_id": it["id"], "to_status": "done"})
    assert _err(res) and "transition" in _text(res).lower()


@pytest.mark.asyncio
async def test_advance_by_query_resolves(client):
    raw, _ = await _token_pid(client)
    await _make_item(client, raw, title="uniquequery widget", area="ui")
    res = await _call(client, raw, "pulsyr_advance", {"query": "uniquequery widget", "to_status": "spec"})
    assert not _err(res)
    assert _data(res)["status"] == "spec"


@pytest.mark.asyncio
async def test_complete_twice_is_idempotent(client):
    raw, _ = await _token_pid(client)
    it = await _make_item(client, raw)
    first = await _call(client, raw, "pulsyr_complete", {"item_id": it["id"]})
    assert not _err(first)
    again = await _call(client, raw, "pulsyr_complete", {"item_id": it["id"]})
    assert not _err(again)  # completing a done item is a no-op, not an error


@pytest.mark.asyncio
async def test_link_missing_relation_errors(client):
    raw, _ = await _token_pid(client)
    a = await _make_item(client, raw, title="src item")
    b = await _make_item(client, raw, title="tgt item")
    res = await _call(client, raw, "pulsyr_link", {"source_id": a["id"], "target_id": b["id"]})
    assert _err(res) and "relation" in _text(res)


@pytest.mark.asyncio
async def test_link_invalid_relation_errors(client):
    raw, _ = await _token_pid(client)
    a = await _make_item(client, raw, title="src2 item")
    b = await _make_item(client, raw, title="tgt2 item")
    res = await _call(client, raw, "pulsyr_link",
                      {"source_id": a["id"], "target_id": b["id"], "relation": "bogus-rel"})
    assert _err(res)


@pytest.mark.asyncio
async def test_link_success(client):
    raw, _ = await _token_pid(client)
    a = await _make_item(client, raw, title="src3 item")
    b = await _make_item(client, raw, title="tgt3 item")
    res = await _call(client, raw, "pulsyr_link",
                      {"source_id": a["id"], "target_id": b["id"], "relation": "blocks"})
    assert not _err(res)
    assert _data(res)["relation"] == "blocks"


# ---------- thread tool error branches ----------

@pytest.mark.asyncio
async def test_thread_advance_not_found_errors(client):
    raw, _ = await _token_pid(client)
    res = await _call(client, raw, "pulsyr_thread_advance", {"thread_id": str(uuid.uuid4())})
    assert _err(res) and "not found" in _text(res).lower()


@pytest.mark.asyncio
async def test_thread_advance_past_end_errors(client):
    raw, _ = await _token_pid(client)
    t = _data(await _call(client, raw, "pulsyr_thread_create", {"area_name": "billing", "title": "Billing"}))
    saw_error = False
    for _ in range(10):
        res = await _call(client, raw, "pulsyr_thread_advance", {"thread_id": t["id"]})
        if _err(res):
            saw_error = True
            break
    assert saw_error  # advancing past the last stage raises ThreadError


@pytest.mark.asyncio
async def test_thread_detail_not_found_errors(client):
    raw, _ = await _token_pid(client)
    res = await _call(client, raw, "pulsyr_thread", {"id": str(uuid.uuid4())})
    assert _err(res) and "not found" in _text(res).lower()


@pytest.mark.asyncio
async def test_thread_link_missing_id_errors(client):
    raw, _ = await _token_pid(client)
    res = await _call(client, raw, "pulsyr_thread_link", {"item_id": str(uuid.uuid4())})
    assert _err(res) and "thread_id" in _text(res)


@pytest.mark.asyncio
async def test_thread_link_not_found_errors(client):
    raw, _ = await _token_pid(client)
    it = await _make_item(client, raw)
    res = await _call(client, raw, "pulsyr_thread_link",
                      {"thread_id": str(uuid.uuid4()), "item_id": it["id"]})
    assert _err(res) and "not found" in _text(res).lower()


# ---------- incident tool branches ----------

async def _seed_incident(client, pid):
    from app.database import get_db
    from app.webhooks.models import SentryIssue

    iid = uuid.uuid4()
    async for db in client.app.dependency_overrides[get_db]():
        db.add(SentryIssue(
            id=iid, project_id=pid, sentry_issue_id=f"S{uuid.uuid4().hex[:8]}",
            title="Boom error", project="proj", level="error", status="new",
            events_count=3, triage="pending",
        ))
        await db.commit()
        break
    return iid


@pytest.mark.asyncio
async def test_incident_detail_without_sentry_token(client):
    raw, pid = await _token_pid(client)
    iid = await _seed_incident(client, pid)
    res = await _call(client, raw, "pulsyr_incident", {"id": str(iid)})
    assert not _err(res)
    data = _data(res)
    # No Sentry API token configured → stack trace fetch fails gracefully.
    assert data["stacktrace"] is None
    assert "detail_error" in data


@pytest.mark.asyncio
async def test_incident_not_found_errors(client):
    raw, _ = await _token_pid(client)
    res = await _call(client, raw, "pulsyr_incident", {"id": str(uuid.uuid4())})
    assert _err(res)


@pytest.mark.asyncio
async def test_incident_resolve_not_found_errors(client):
    raw, _ = await _token_pid(client)
    res = await _call(client, raw, "pulsyr_incident_resolve", {"id": str(uuid.uuid4())})
    assert _err(res)


@pytest.mark.asyncio
async def test_incidents_list_status_all(client):
    raw, pid = await _token_pid(client)
    await _seed_incident(client, pid)
    res = await _call(client, raw, "pulsyr_incidents", {"status": "all"})
    assert not _err(res)
    assert len(_data(res)) >= 1


# ---------- pulsyr_context with area fallback (no quickwins -> p0/p1) ----------

@pytest.mark.asyncio
async def test_context_area_fallback(client):
    raw, _ = await _token_pid(client)
    await _make_item(client, raw, title="urgent thing", area="infra")
    res = await _call(client, raw, "pulsyr_context", {"area": "infra"})
    assert not _err(res)
    assert "local" in _data(res)


# ---------- REST /api/v1/items branches (token-scoped) ----------

def _h(raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


@pytest.mark.asyncio
async def test_rest_get_item_with_comment(client):
    raw, _ = await _token_pid(client)
    it = await _make_item(client, raw)
    iid = it["id"]
    c = await client.post(f"/api/v1/items/{iid}/comments", json={"body_md": "a note"}, headers=_h(raw))
    assert c.status_code == 201
    g = await client.get(f"/api/v1/items/{iid}", headers=_h(raw))
    assert g.status_code == 200
    assert any(cm["body_md"] == "a note" for cm in g.json()["comments"])


@pytest.mark.asyncio
async def test_rest_get_comment_not_found(client):
    raw, _ = await _token_pid(client)
    it = await _make_item(client, raw)
    r = await client.get(f"/api/v1/items/{it['id']}/comments/{uuid.uuid4()}", headers=_h(raw))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_rest_close_then_invalid_close(client):
    raw, _ = await _token_pid(client)
    it = await _make_item(client, raw)
    ok = await client.post(f"/api/v1/items/{it['id']}/close",
                           json={"status": "done", "reason": "shipped"}, headers=_h(raw))
    assert ok.status_code == 200
    bad = await client.post(f"/api/v1/items/{it['id']}/close",
                            json={"status": "discarded"}, headers=_h(raw))
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_rest_reopen_open_item_errors(client):
    raw, _ = await _token_pid(client)
    it = await _make_item(client, raw)
    r = await client.post(f"/api/v1/items/{it['id']}/reopen", headers=_h(raw))
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_rest_relationship_target_not_in_project(client):
    raw, _ = await _token_pid(client)
    it = await _make_item(client, raw)
    r = await client.post(
        "/api/v1/items/relationships",
        json={"source_id": it["id"], "target_id": str(uuid.uuid4()), "relation": "blocks"},
        headers=_h(raw),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_rest_enqueue_enrich(client):
    raw, _ = await _token_pid(client)
    it = await _make_item(client, raw)
    r = await client.post(f"/api/v1/items/{it['id']}/enrich", headers=_h(raw))
    assert r.status_code == 202
    assert "run_id" in r.json()


# ---------- owner-session endpoints (require_owner) ----------

async def _owner_cookie(client) -> dict:
    from app.auth.service import create_user
    from app.database import get_db

    email = f"own{uuid.uuid4().hex[:8]}@t.cl"
    async for db in client.app.dependency_overrides[get_db]():
        await create_user(db, email, "Own", "passw0rd", "admin")  # auto account+owner+Default project
        break
    r = await client.post("/login", data={"email": email, "password": "passw0rd"},
                          follow_redirects=False)
    return dict(r.cookies)


@pytest.mark.asyncio
async def test_enqueue_pending_enrich_owner(client):
    cookies = await _owner_cookie(client)
    r = await client.post("/api/v1/items/enrich-pending", cookies=cookies)
    assert r.status_code == 202
    assert "queued" in r.json()


@pytest.mark.asyncio
async def test_import_digest_requires_path(client):
    cookies = await _owner_cookie(client)
    r = await client.post("/api/v1/items/import/digest", json={}, cookies=cookies)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_import_digest_path_not_found(client):
    cookies = await _owner_cookie(client)
    r = await client.post("/api/v1/items/import/digest",
                          json={"path": "/nonexistent/does-not-exist.jsonl"}, cookies=cookies)
    assert r.status_code == 404


# ---------- REST /api/v1/threads (stage, artifact, elaborate) ----------

@pytest.mark.asyncio
async def test_rest_thread_stage_and_artifact(client):
    raw, _ = await _token_pid(client)
    c = await client.post("/api/v1/threads",
                          json={"scope_name": "billing", "title": "Billing thread"}, headers=_h(raw))
    assert c.status_code == 201
    tid = c.json()["id"]
    s = await client.post(f"/api/v1/threads/{tid}/stage",
                          json={"stage": "research"}, headers=_h(raw))
    assert s.status_code == 200
    a = await client.post(f"/api/v1/threads/{tid}/artifacts",
                          json={"kind": "notes", "content": "some notes"}, headers=_h(raw))
    assert a.status_code == 201


@pytest.mark.asyncio
async def test_rest_thread_elaborate_responds(client):
    raw, _ = await _token_pid(client)
    c = await client.post("/api/v1/threads",
                          json={"scope_name": "ml", "title": "ML thread"}, headers=_h(raw))
    tid = c.json()["id"]
    r = await client.post(f"/api/v1/threads/{tid}/elaborate-stage", headers=_h(raw))
    # Either drafts a stage (200) or errors gracefully without an LLM key (422) — never 500.
    assert r.status_code in (200, 422)
