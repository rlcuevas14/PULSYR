import asyncio
import hashlib
import time
import uuid

import pytest


@pytest.mark.asyncio
async def test_collection_limits_are_bounded(client):
    from app.accounts.models import Account
    from app.auth.models import ApiToken
    from app.auth.service import create_user
    from app.database import SessionFactory
    from app.projects.models import Project

    raw = f"limit-{uuid.uuid4()}"
    async with SessionFactory() as db:
        suffix = uuid.uuid4().hex[:6]
        account = Account(name="limits", slug=f"limits-{suffix}")
        db.add(account)
        await db.flush()
        user = await create_user(
            db, f"limits-{suffix}@test.dev", "Limits", "password",
            account_id=account.id, account_role="owner",
        )
        project = Project(
            name="limits", slug=f"limits-{suffix}", account_id=account.id
        )
        db.add(project)
        await db.flush()
        from app.projects.modules import initialize_modules

        await initialize_modules(db, project.id, "product", user.email)
        db.add(ApiToken(
            name="limits", token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            scopes="write", project_id=project.id, created_by=user.id,
        ))
        await db.commit()

    headers = {"Authorization": f"Bearer {raw}"}
    assert (await client.get("/api/v1/items?limit=201", headers=headers)).status_code == 422
    assert (await client.get("/api/v1/items?offset=10001", headers=headers)).status_code == 422
    assert (await client.get("/api/v1/scopes?limit=201", headers=headers)).status_code == 422
    assert (await client.get("/api/v1/threads?limit=201", headers=headers)).status_code == 422
    assert (await client.get("/api/v1/items/search?q=x&limit=201", headers=headers)).status_code == 422


@pytest.mark.asyncio
async def test_thread_scope_lookup_is_project_scoped(db):
    from app.accounts.models import Account
    from app.projects.models import Project
    from app.scopes.models import Scope
    from app.threads.models import Thread
    from app.threads.service import list_threads

    left_account = Account(name="left", slug=f"left-{uuid.uuid4().hex[:6]}")
    right_account = Account(name="right", slug=f"right-{uuid.uuid4().hex[:6]}")
    db.add_all([left_account, right_account])
    await db.flush()
    left = Project(
        name="left", slug=f"left-{uuid.uuid4().hex[:6]}", account_id=left_account.id
    )
    right = Project(
        name="right", slug=f"right-{uuid.uuid4().hex[:6]}", account_id=right_account.id
    )
    db.add_all([left, right])
    await db.flush()
    left_scope = Scope(name="shared", project_id=left.id)
    right_scope = Scope(name="shared", project_id=right.id)
    db.add_all([left_scope, right_scope])
    await db.flush()
    db.add_all([
        Thread(project_id=left.id, scope_id=left_scope.id, title="left"),
        Thread(project_id=right.id, scope_id=right_scope.id, title="right"),
    ])
    await db.flush()

    rows = await list_threads(db, scope_name="shared", project_id=right.id)
    assert [thread.title for thread in rows] == ["right"]


@pytest.mark.asyncio
async def test_bounded_collection_load_smoke(client):
    """A small concurrent read burst remains successful and within a broad CI budget."""
    from tests.test_items_rest import _token

    raw, _project_id = await _token(client)
    headers = {"Authorization": f"Bearer {raw}"}

    async def request_once():
        started = time.perf_counter()
        response = await client.get("/api/v1/items?limit=50&order=recent", headers=headers)
        return response.status_code, (time.perf_counter() - started) * 1000

    results = await asyncio.gather(*(request_once() for _ in range(32)))
    elapsed = sorted(duration for _status, duration in results)
    p95 = elapsed[int(len(elapsed) * 0.95) - 1]
    assert all(status == 200 for status, _duration in results)
    assert p95 < 5000
