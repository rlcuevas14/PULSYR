"""Account isolation: a user of account A can never read or mutate account B's data."""
import uuid

import pytest
from httpx import AsyncClient


async def _account_with_data(client: AsyncClient, label: str) -> dict:
    """Create account + owner + project + a scope/item/thread + a write MCP token."""
    from app.accounts.service import create_account
    from app.auth.service import create_api_token
    from app.database import get_db
    from app.items.models import Item
    from app.projects.service import create_project
    from app.scopes.models import Scope
    from app.threads.models import Thread

    suffix = uuid.uuid4().hex[:8]
    data: dict = {}
    async for db in client.app.dependency_overrides[get_db]():
        acc, owner = await create_account(
            db, f"{label}-{suffix}", f"{label}{suffix}@t.cl", "Owner", "password123"
        )
        project = await create_project(db, name=f"{label}-proj-{suffix}", account_id=acc.id)
        await db.flush()
        scope = Scope(name=f"{label}-area", project_id=project.id)
        db.add(scope)
        await db.flush()
        item = Item(
            scope_id=scope.id, project_id=project.id, title=f"{label}-secret-item",
            type="feature", status="backlog", origen="human",
        )
        thread = Thread(
            scope_id=scope.id, project_id=project.id, title=f"{label}-secret-thread", stage="idea",
        )
        db.add(item)
        db.add(thread)
        await db.flush()
        tok, raw = await create_api_token(db, f"{label}-tok", "write", owner.id)
        tok.project_id = project.id
        await db.commit()
        data = {
            "email": f"{label}{suffix}@t.cl",
            "project_id": str(project.id),
            "scope_name": f"{label}-area",
            "item_id": str(item.id),
            "item_title": f"{label}-secret-item",
            "thread_id": str(thread.id),
            "token": raw,
        }
        break
    return data


async def _login(client: AsyncClient, email: str) -> dict:
    r = await client.post(
        "/auth/login", data={"email": email, "password": "password123"}, follow_redirects=False
    )
    return dict(r.cookies)


@pytest.mark.asyncio
async def test_session_user_cannot_read_other_account(client: AsyncClient):
    a = await _account_with_data(client, "acctA")
    b = await _account_with_data(client, "acctB")
    cookies = await _login(client, a["email"])

    scopes = (await client.get("/api/v1/scopes", cookies=cookies)).json()
    assert all(s["name"] != b["scope_name"] for s in scopes)
    items = (await client.get("/api/v1/items", cookies=cookies)).json()
    assert all(i["title"] != b["item_title"] for i in items)
    threads = (await client.get("/api/v1/threads", cookies=cookies)).json()
    assert all(t["id"] != b["thread_id"] for t in threads)

    # Direct reads of B's rows → 404 (REST + UI), never leaking existence.
    assert (await client.get(f"/api/v1/items/{b['item_id']}", cookies=cookies)).status_code == 404
    assert (await client.get(f"/api/v1/threads/{b['thread_id']}", cookies=cookies)).status_code == 404
    assert (await client.get(f"/items/{b['item_id']}", cookies=cookies)).status_code == 404
    assert (await client.get(f"/threads/{b['thread_id']}", cookies=cookies)).status_code == 404


@pytest.mark.asyncio
async def test_session_user_cannot_mutate_other_account_item(client: AsyncClient):
    a = await _account_with_data(client, "mutA")
    b = await _account_with_data(client, "mutB")
    cookies = await _login(client, a["email"])
    # PATCH B's item via A's session → 404 (not visible / not accessible).
    r = await client.patch(
        f"/api/v1/items/{b['item_id']}", json={"title": "hijacked"}, cookies=cookies
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_session_user_cannot_switch_to_other_account_project(client: AsyncClient):
    a = await _account_with_data(client, "swA")
    b = await _account_with_data(client, "swB")
    cookies = await _login(client, a["email"])
    # Attempt to switch the session to B's project — must not take effect.
    await client.post(
        "/ui/project/switch", data={"project_id": b["project_id"]},
        cookies=cookies, follow_redirects=False,
    )
    items = (await client.get("/api/v1/items", cookies=cookies)).json()
    assert all(i["title"] != b["item_title"] for i in items)


@pytest.mark.asyncio
async def test_mcp_token_cannot_read_other_account(client: AsyncClient):
    a = await _account_with_data(client, "mcpA")
    b = await _account_with_data(client, "mcpB")
    hdr = {"Authorization": f"Bearer {a['token']}"}
    r = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "pulsyr_list", "arguments": {}}},
        headers=hdr,
    )
    result = r.json()["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert b["item_title"] not in text   # B's data must never surface
    assert a["item_title"] in text       # sanity: A sees its own


@pytest.mark.asyncio
async def test_two_accounts_can_share_area_name(client: AsyncClient):
    """Per-project area uniqueness: two projects/accounts may each have a 'backend' area."""
    from sqlalchemy import func, select

    from app.database import get_db
    from app.projects.models import Project
    from app.scopes.models import Scope

    a = await _account_with_data(client, "shareA")
    b = await _account_with_data(client, "shareB")
    async for db in client.app.dependency_overrides[get_db]():
        pa = await db.get(Project, uuid.UUID(a["project_id"]))
        pb = await db.get(Project, uuid.UUID(b["project_id"]))
        db.add(Scope(name="backend", project_id=pa.id))
        db.add(Scope(name="backend", project_id=pb.id))
        await db.commit()  # must NOT raise — per-project unique, not global
        n = await db.scalar(select(func.count()).select_from(Scope).where(Scope.name == "backend"))
        assert n == 2
        break
