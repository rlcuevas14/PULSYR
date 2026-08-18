"""Management (PMO) domain: MCP tools end-to-end + service audit/isolation/limits.

MCP tests go through the real /mcp stack (auth, write-scope, project failsafe, commit).
Service tests use the db fixture for audit-event and size-limit assertions. DB-backed →
runs in CI (pgvector/pgvector:pg16); the pure Gantt geometry is in test_management_gantt.py.
"""
import base64
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
        proj = await create_project(db, name=f"p{s}", account_id=acc.id, preset="client")
        tok, raw = await create_api_token(db, f"tok{s}", scopes, owner.id)
        tok.project_id = proj.id
        await db.commit()
        return raw, proj.id


async def _call(client, raw, name, args):
    r = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": name, "arguments": args}},
        headers={"Authorization": f"Bearer {raw}"},
    )
    return r.json()["result"]


def _text(result):
    return result["content"][0]["text"]


def _data(result):
    return json.loads(_text(result))


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


@pytest.mark.asyncio
async def test_document_versioning_and_dedup(client: AsyncClient):
    raw, _pid = await _setup(client)

    # create (v1)
    d = _data(await _call(client, raw, "pulsyr_doc_put", {
        "compartment": "Proposals", "name": "Client proposal", "doc_type": "md",
        "content": "# v1", "summary_md": "first draft"}))
    assert d["current_version"] == 1 and d["new_version"] is True

    # identical bytes → dedup, no new version
    d = _data(await _call(client, raw, "pulsyr_doc_put", {
        "compartment": "Proposals", "name": "Client proposal", "doc_type": "md", "content": "# v1"}))
    assert d["current_version"] == 1 and d["new_version"] is False

    # changed bytes → v2
    d = _data(await _call(client, raw, "pulsyr_doc_put", {
        "compartment": "Proposals", "name": "Client proposal", "doc_type": "md", "content": "# v2"}))
    assert d["current_version"] == 2 and d["new_version"] is True

    listed = _data(await _call(client, raw, "pulsyr_doc_list", {}))
    assert len(listed) == 1 and listed[0]["compartment"] == "Proposals"

    got = _data(await _call(client, raw, "pulsyr_doc_get",
                            {"deliverable_id": d["id"], "include_content": True}))
    assert len(got["versions"]) == 2
    assert got["content_text"] == "# v2"  # current version inlined for md


@pytest.mark.asyncio
async def test_document_invalid_type_and_binary(client: AsyncClient):
    raw, _pid = await _setup(client)
    bad = await _call(client, raw, "pulsyr_doc_put",
                      {"compartment": "C", "name": "x", "doc_type": "exe", "content": "z"})
    assert bad["isError"] is True and "doc_type" in _text(bad)

    # binary via base64
    d = _data(await _call(client, raw, "pulsyr_doc_put", {
        "compartment": "C", "name": "sheet", "doc_type": "xlsx",
        "content_base64": _b64("binary-bytes")}))
    assert d["doc_type"] == "xlsx"


@pytest.mark.asyncio
async def test_pendings_lifecycle(client: AsyncClient):
    raw, _pid = await _setup(client)
    p = _data(await _call(client, raw, "pulsyr_pending_upsert",
                          {"title": "Send contract", "owner": "Rodolfo", "due_date": "2026-08-01"}))
    assert p["status"] == "open" and p["owner"] == "Rodolfo"

    rows = _data(await _call(client, raw, "pulsyr_pending_list", {}))
    assert len(rows) == 1

    done = _data(await _call(client, raw, "pulsyr_pending_complete", {"pending_id": p["id"]}))
    assert done["status"] == "done"

    # invalid status rejected
    bad = await _call(client, raw, "pulsyr_pending_upsert", {"title": "x", "status": "nope"})
    assert bad["isError"] is True


@pytest.mark.asyncio
async def test_gantt_hierarchy_depth_and_cycle(client: AsyncClient):
    raw, _pid = await _setup(client)
    phase = _data(await _call(client, raw, "pulsyr_gantt_task_upsert",
                              {"name": "Phase 1", "start_date": "2026-01-05", "end_date": "2026-03-01"}))
    sub = _data(await _call(client, raw, "pulsyr_gantt_task_upsert",
                            {"name": "Module A", "parent_id": phase["id"]}))
    task = _data(await _call(client, raw, "pulsyr_gantt_task_upsert",
                             {"name": "Build", "parent_id": sub["id"],
                              "start_date": "2026-01-05", "end_date": "2026-01-20", "progress": 40}))

    # 4th level rejected
    too_deep = await _call(client, raw, "pulsyr_gantt_task_upsert",
                           {"name": "Too deep", "parent_id": task["id"]})
    assert too_deep["isError"] is True and "depth" in _text(too_deep)

    # cycle rejected: reparent phase under its own descendant
    cycle = await _call(client, raw, "pulsyr_gantt_task_upsert",
                        {"task_id": phase["id"], "parent_id": sub["id"]})
    assert cycle["isError"] is True

    plan = _data(await _call(client, raw, "pulsyr_gantt_get", {}))
    assert len(plan["tasks"]) == 3
    assert plan["start"] == "2026-01-05"

    # remove phase cascades to children → empty plan
    _data(await _call(client, raw, "pulsyr_gantt_task_remove", {"task_id": phase["id"]}))
    plan = _data(await _call(client, raw, "pulsyr_gantt_get", {}))
    assert plan["tasks"] == []


@pytest.mark.asyncio
async def test_milestone_and_dates_validation(client: AsyncClient):
    raw, _pid = await _setup(client)
    ms = _data(await _call(client, raw, "pulsyr_gantt_task_upsert",
                           {"name": "Go-live", "is_milestone": True, "start_date": "2026-02-01"}))
    assert ms["is_milestone"] is True
    bad = await _call(client, raw, "pulsyr_gantt_task_upsert",
                      {"name": "Bad", "start_date": "2026-02-10", "end_date": "2026-02-01"})
    assert bad["isError"] is True


@pytest.mark.asyncio
async def test_write_scope_required(client: AsyncClient):
    raw, _pid = await _setup(client, scopes="read")
    res = await _call(client, raw, "pulsyr_doc_put",
                      {"compartment": "C", "name": "x", "doc_type": "md", "content": "y"})
    assert res["isError"] is True and "write" in _text(res)
    # read tools still work
    ok = await _call(client, raw, "pulsyr_pending_list", {})
    assert ok.get("isError") is not True


@pytest.mark.asyncio
async def test_project_isolation(client: AsyncClient):
    raw_a, _ = await _setup(client)
    raw_b, _ = await _setup(client)
    made = _data(await _call(client, raw_a, "pulsyr_doc_put",
                             {"compartment": "A", "name": "secret", "doc_type": "md", "content": "z"}))
    # project B sees nothing and cannot fetch A's deliverable
    assert _data(await _call(client, raw_b, "pulsyr_doc_list", {})) == []
    cross = await _call(client, raw_b, "pulsyr_doc_get", {"deliverable_id": made["id"]})
    assert cross["isError"] is True


# ---------- Service-level (audit + limits) ----------

async def _seed_project(db):
    from app.accounts.service import create_account
    from app.projects.service import create_project
    s = uuid.uuid4().hex[:8]
    acc, _owner = await create_account(db, f"a{s}", f"u{s}@t.cl", "T", "password")
    proj = await create_project(db, name=f"p{s}", account_id=acc.id)
    await db.flush()
    return proj.id


@pytest.mark.asyncio
async def test_audit_event_emitted_on_put(db):
    from sqlalchemy import select

    from app.management import service as mgmt
    from app.management.models import ManagementEvent

    pid = await _seed_project(db)
    d, created = await mgmt.put_deliverable(
        db, pid, compartment_name="C", name="doc", doc_type="md",
        content=b"# hi", actor="tester@t.cl")
    assert created is True
    events = (await db.execute(
        select(ManagementEvent).where(ManagementEvent.entity_id == d.id))).scalars().all()
    assert any(e.action == "created" for e in events)


@pytest.mark.asyncio
async def test_size_limit_enforced(db):
    from app.enums import DELIVERABLE_MAX_BYTES
    from app.management import service as mgmt

    pid = await _seed_project(db)
    with pytest.raises(mgmt.ManagementError):
        await mgmt.put_deliverable(
            db, pid, compartment_name="C", name="huge", doc_type="pdf",
            content=b"x" * (DELIVERABLE_MAX_BYTES + 1), actor="t@t.cl")


@pytest.mark.asyncio
async def test_management_error_paths(client: AsyncClient):
    raw, _pid = await _setup(client)
    # documents
    assert (await _call(client, raw, "pulsyr_doc_put",
                        {"compartment": "C", "name": "", "doc_type": "md", "content": "x"}))["isError"]
    assert (await _call(client, raw, "pulsyr_doc_put",
                        {"compartment": "C", "name": "n", "doc_type": "md"}))["isError"]
    assert (await _call(client, raw, "pulsyr_doc_get", {"deliverable_id": "not-a-uuid"}))["isError"]
    # doc_list with a status filter exercises the filtered query branch
    assert _data(await _call(client, raw, "pulsyr_doc_list", {"status": "final"})) == []
    # pendings
    assert (await _call(client, raw, "pulsyr_pending_upsert",
                        {"title": "x", "plan_task_id": str(uuid.uuid4())}))["isError"]
    assert (await _call(client, raw, "pulsyr_pending_upsert", {}))["isError"]
    # gantt
    assert (await _call(client, raw, "pulsyr_gantt_task_upsert", {"name": "x", "progress": 150}))["isError"]
    assert (await _call(client, raw, "pulsyr_gantt_task_remove", {"task_id": str(uuid.uuid4())}))["isError"]
    assert (await _call(client, raw, "pulsyr_gantt_task_upsert",
                        {"name": "x", "parent_id": str(uuid.uuid4())}))["isError"]
    assert (await _call(client, raw, "pulsyr_gantt_task_upsert",
                        {"task_id": str(uuid.uuid4()), "name": "y"}))["isError"]
    assert (await _call(client, raw, "pulsyr_gantt_task_upsert", {"deps": "not-a-list"}))["isError"]


@pytest.mark.asyncio
async def test_rollback_creates_new_version(db):
    from app.management import service as mgmt

    pid = await _seed_project(db)
    d, _ = await mgmt.put_deliverable(db, pid, compartment_name="C", name="doc",
                                      doc_type="md", content=b"v1", actor="t@t.cl")
    await mgmt.put_deliverable(db, pid, compartment_name="C", name="doc",
                               doc_type="md", content=b"v2", actor="t@t.cl")
    assert d.current_version == 2
    d = await mgmt.rollback_deliverable(db, pid, d.id, 1, "t@t.cl")
    assert d.current_version == 3
    _, v = await mgmt.get_version(db, pid, d.id, 3)
    assert v.content == b"v1"  # restored bytes
