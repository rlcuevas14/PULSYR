"""Management UI screens + /ui/management actions (cookie session), mirroring test_ui.py.

Exercises the documentos/plan/pendientes screens, deliverable upload/download/preview/rollback,
pending CRUD + group-by, and the Gantt render — the router paths the MCP tests don't touch.
"""
import uuid
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.database import get_db


async def _login(client: AsyncClient, role: str = "admin"):
    from app.auth.service import create_user
    from app.projects.models import Project

    suffix = uuid.uuid4().hex[:8]
    email = f"mui{suffix}@t.cl"
    async for db in client.app.dependency_overrides[get_db]():
        user = await create_user(db, email, "MUI User", "password", role)
        proj = (await db.execute(
            select(Project).where(Project.account_id == user.account_id)
        )).scalars().first()
        uid, pid = user.id, proj.id
        break
    resp = await client.post(
        "/login", data={"email": email, "password": "password"}, follow_redirects=False
    )
    assert resp.status_code == 303
    return uid, pid


async def _first_deliverable(client, pid):
    from app.management.models import Deliverable
    async for db in client.app.dependency_overrides[get_db]():
        return (await db.execute(
            select(Deliverable).where(Deliverable.project_id == pid)
        )).scalars().first()


async def _upload(client, *, compartment="Docs", filename="proposal.md",
                  content=b"# Hello", status="draft"):
    return await client.post(
        "/ui/management/documentos/upload",
        files={"file": (filename, content, "application/octet-stream")},
        data={"compartment": compartment, "status": status, "owner": "Rodolfo",
              "summary_md": "a summary", "note": "v"},
        follow_redirects=False,
    )


@pytest.mark.asyncio
async def test_management_home_redirects(client: AsyncClient):
    await _login(client)
    r = await client.get("/management", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/management/pendientes"


@pytest.mark.asyncio
async def test_documentos_screen_and_compartment(client: AsyncClient):
    _uid, _pid = await _login(client)
    r = await client.get("/management/documentos")
    assert r.status_code == 200

    r = await client.post("/ui/management/compartments",
                          data={"name": "Contracts", "description": "legal"},
                          follow_redirects=False)
    assert r.status_code == 303
    r = await client.get("/management/documentos")
    assert "Contracts" in r.text


@pytest.mark.asyncio
async def test_upload_download_preview_rollback(client: AsyncClient):
    _uid, pid = await _login(client)

    # upload v1 (md → text preview branch)
    assert (await _upload(client, content=b"# Draft v1")).status_code == 303
    d = await _first_deliverable(client, pid)
    assert d is not None and d.current_version == 1

    # detail renders (md preview) + filtered list + search
    assert (await client.get(f"/management/documentos/{d.id}")).status_code == 200
    assert (await client.get(f"/management/documentos?compartment={d.compartment_id}")).status_code == 200
    assert (await client.get("/management/documentos?q=proposal")).status_code == 200

    # download (attachment + inline)
    dl = await client.get(f"/management/documentos/{d.id}/download")
    assert dl.status_code == 200 and dl.content == b"# Draft v1"
    assert "attachment" in dl.headers["content-disposition"]
    inline = await client.get(f"/management/documentos/{d.id}/download?disposition=inline")
    assert "inline" in inline.headers["content-disposition"]

    # upload v2 (same name/compartment → new version), then rollback to v1
    assert (await _upload(client, content=b"# Draft v2")).status_code == 303
    d = await _first_deliverable(client, pid)
    assert d.current_version == 2
    rb = await client.post(f"/ui/management/documentos/{d.id}/rollback",
                           data={"version_no": "1"}, follow_redirects=False)
    assert rb.status_code == 303
    d = await _first_deliverable(client, pid)
    assert d.current_version == 3  # rollback appends a new version

    # missing download → 404
    assert (await client.get(f"/management/documentos/{uuid.uuid4()}/download")).status_code == 404


@pytest.mark.asyncio
async def test_upload_invalid_type_rejected(client: AsyncClient):
    await _login(client)
    r = await _upload(client, filename="virus.exe", content=b"x")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_html_and_binary_preview_branches(client: AsyncClient):
    _uid, pid = await _login(client)
    await _upload(client, compartment="Web", filename="page.html", content=b"<h1>hi</h1>")
    await _upload(client, compartment="Sheets", filename="data.xlsx", content=b"PKxx")
    from app.management.models import Deliverable
    async for db in client.app.dependency_overrides[get_db]():
        docs = (await db.execute(select(Deliverable).where(Deliverable.project_id == pid))).scalars().all()
        break
    for d in docs:
        assert (await client.get(f"/management/documentos/{d.id}")).status_code == 200


@pytest.mark.asyncio
async def test_pendientes_crud_and_groupings(client: AsyncClient):
    _uid, pid = await _login(client)

    # create
    r = await client.post("/ui/management/pendientes",
                          data={"title": "Call client", "owner": "R", "status": "open",
                                "due_date": "2026-08-01"}, follow_redirects=False)
    assert r.status_code == 303

    from app.management.models import Pending
    async for db in client.app.dependency_overrides[get_db]():
        p = (await db.execute(select(Pending).where(Pending.project_id == pid))).scalars().first()
        pend_id = p.id
        break

    # edit (pending_id present) → doing
    r = await client.post("/ui/management/pendientes",
                          data={"pending_id": str(pend_id), "title": "Call client",
                                "status": "doing", "owner": "R"}, follow_redirects=False)
    assert r.status_code == 303

    # every group-by variant + overdue chip renders
    for group in ("status", "owner", "due", "none"):
        assert (await client.get(f"/management/pendientes?group={group}")).status_code == 200
    assert (await client.get("/management/pendientes?overdue=true&status=doing")).status_code == 200
    assert (await client.get("/management/pendientes?owner=R&group=owner")).status_code == 200

    # complete (204 + HX-Refresh)
    c = await client.post(f"/ui/management/pendientes/{pend_id}/complete")
    assert c.status_code == 204 and c.headers.get("HX-Refresh") == "true"

    # delete (204)
    d = await client.post(f"/ui/management/pendientes/{pend_id}/delete")
    assert d.status_code == 204


@pytest.mark.asyncio
async def test_plan_screen_empty_and_rendered(client: AsyncClient):
    _uid, pid = await _login(client)

    # empty plan
    r = await client.get("/management/plan")
    assert r.status_code == 200

    # seed a 3-level plan crossing week 12 (weekly→monthly axis) + a milestone
    from app.management import service as mgmt
    async for db in client.app.dependency_overrides[get_db]():
        phase = await mgmt.upsert_plan_task(db, pid, actor="t", name="Phase 1",
                                            start_date=date(2026, 1, 5), end_date=date(2026, 6, 1))
        await mgmt.upsert_plan_task(db, pid, actor="t", name="Task A", parent_id=phase.id,
                                    start_date=date(2026, 1, 5), end_date=date(2026, 2, 1),
                                    progress=50, deps=[])
        await mgmt.upsert_plan_task(db, pid, actor="t", name="Go-live", is_milestone=True,
                                    start_date=date(2026, 3, 2))
        await db.commit()
        break

    r = await client.get("/management/plan")
    assert r.status_code == 200
    assert "gantt-grid" in r.text and "Phase 1" in r.text
