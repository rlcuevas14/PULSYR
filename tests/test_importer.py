from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.items.models import Item
from app.scopes.models import Scope

FIXTURE = Path(__file__).parent / "fixtures" / "seed_sample.jsonl"


@pytest.fixture(autouse=True)
async def _truncate_items(db: AsyncSession):
    """Limpia items y scopes antes de cada test del importer para evitar colisiones de count."""
    await db.execute(text("TRUNCATE items, scopes RESTART IDENTITY CASCADE"))
    await db.commit()
    yield


@pytest.mark.asyncio
async def test_import_creates_scopes_and_items(db: AsyncSession):
    from app.items.importer import import_jsonl

    result = await import_jsonl(db, FIXTURE)
    assert result["imported"] == 2   # 3 líneas pero 1 duplicada
    assert result["skipped_duplicate"] == 1

    count = await db.scalar(select(func.count()).select_from(Item))
    assert count == 2

    scope = await db.scalar(select(Scope).where(Scope.name == "ia-chat"))
    assert scope is not None


@pytest.mark.asyncio
async def test_import_is_idempotent(db: AsyncSession):
    from app.items.importer import import_jsonl

    r1 = await import_jsonl(db, FIXTURE)
    r2 = await import_jsonl(db, FIXTURE)
    assert r2["imported"] == 0
    assert r2["skipped_duplicate"] == r1["imported"] + r1["skipped_duplicate"]


@pytest.mark.asyncio
async def test_diferido_maps_to_backlog(db: AsyncSession):
    from app.items.importer import import_jsonl

    await import_jsonl(db, FIXTURE)
    item = await db.scalar(
        select(Item).where(Item.title == "Refactor de prompts")
    )
    assert item is not None
    assert item.status == "backlog"
    assert item.priority_declared.startswith("DIFERIDO")


@pytest.mark.asyncio
async def test_stale_risk_preserved(db: AsyncSession):
    from app.items.importer import import_jsonl

    await import_jsonl(db, FIXTURE)
    item = await db.scalar(select(Item).where(Item.title == "Refactor de prompts"))
    assert item.stale_risk is True


@pytest.mark.asyncio
async def test_import_endpoint(client):
    from app.auth.service import create_user
    from app.database import get_db

    async for db in client.app.dependency_overrides[get_db]():
        await create_user(db, "importadmin@test.cl", "Admin", "pass", "admin")
        break

    cookies_resp = await client.post(
        "/login",
        data={"email": "importadmin@test.cl", "password": "pass"},
        follow_redirects=False,
    )
    cookies = dict(cookies_resp.cookies)

    resp = await client.post(
        "/api/v1/items/import/digest",
        json={"path": str(FIXTURE)},
        cookies=cookies,
    )
    assert resp.status_code == 200
    assert resp.json()["imported"] >= 1
