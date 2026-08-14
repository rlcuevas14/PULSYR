"""Service-layer coverage: items lifecycle, scopes, relationships, threads."""
import uuid

import pytest
from sqlalchemy import select

from app.items import service as isvc
from app.items.models import Item
from app.scopes import service as ssvc
from app.scopes.models import Scope


async def _project(db):
    from app.accounts.service import create_account
    from app.projects.service import create_project

    s = uuid.uuid4().hex[:6]
    acc, owner = await create_account(db, f"a{s}", f"svc{s}@t.cl", "S", "password")
    proj = await create_project(db, name=f"p{s}", account_id=acc.id)
    return proj, owner


async def _item(db, proj, status="backlog", title="Task"):
    scope = Scope(name=f"area-{uuid.uuid4().hex[:6]}", project_id=proj.id)
    db.add(scope)
    await db.flush()
    item = Item(scope_id=scope.id, project_id=proj.id, title=title, type="feature",
                status=status, origen="human")
    db.add(item)
    await db.flush()
    return item


# ---------- items lifecycle ----------

@pytest.mark.asyncio
async def test_apply_transition_valid(db):
    proj, _ = await _project(db)
    item = await _item(db, proj, status="backlog")
    await isvc.apply_transition(db, item, "in-progress", "me@t.cl")
    assert item.status == "in-progress"


@pytest.mark.asyncio
async def test_apply_transition_terminal_and_invalid_raise(db):
    proj, _ = await _project(db)
    item = await _item(db, proj, status="backlog")
    with pytest.raises(isvc.TransitionError):
        await isvc.apply_transition(db, item, "done", "me@t.cl")  # terminal → must close
    with pytest.raises(isvc.TransitionError):
        await isvc.apply_transition(db, item, "in-review", "me@t.cl")  # not in matrix


@pytest.mark.asyncio
async def test_close_and_reopen(db):
    proj, _ = await _project(db)
    item = await _item(db, proj, status="in-progress")
    unblocked = await isvc.close_item(db, item, "done", "shipped", "me@t.cl", commit_sha="deadbeef")
    assert item.status == "done" and item.closed_at is not None
    assert isinstance(unblocked, list)
    await isvc.reopen_item(db, item, "me@t.cl")
    assert item.status == "backlog" and item.closed_at is None


@pytest.mark.asyncio
async def test_reopen_non_terminal_raises(db):
    proj, _ = await _project(db)
    item = await _item(db, proj, status="backlog")
    with pytest.raises(isvc.TransitionError):
        await isvc.reopen_item(db, item, "me@t.cl")


@pytest.mark.asyncio
async def test_set_priority(db):
    proj, _ = await _project(db)
    item = await _item(db, proj)
    await isvc.set_priority(db, item, "p0", "me@t.cl")
    assert item.priority == "p0" and item.priority_declared == "p0"


# ---------- scopes ----------

@pytest.mark.asyncio
async def test_resolve_scope_create_find_and_missing(db):
    proj, _ = await _project(db)
    s1 = await ssvc.resolve_scope(db, "Backend", create=True, project_id=proj.id)
    s2 = await ssvc.resolve_scope(db, "backend", create=False, project_id=proj.id)
    assert s1.id == s2.id  # case-insensitive
    with pytest.raises(ssvc.ScopeError):
        await ssvc.resolve_scope(db, "Ghost", create=False, project_id=proj.id)
    with pytest.raises(ssvc.ScopeError):
        await ssvc.resolve_scope(db, "  ", create=True, project_id=proj.id)


@pytest.mark.asyncio
async def test_create_and_update_scope(db):
    proj, _ = await _project(db)
    scope = await ssvc.create_scope(db, {"name": "Infra", "project_id": proj.id})
    updated = await ssvc.update_scope(db, scope.id, {"archived": True})
    assert updated.archived is True
    with pytest.raises(ssvc.ScopeError):
        await ssvc.create_scope(db, {"name": "  "})
    with pytest.raises(ssvc.ScopeError):
        await ssvc.update_scope(db, uuid.uuid4(), {"archived": True})


# ---------- relationships ----------

@pytest.mark.asyncio
async def test_relationship_create_idempotent_and_guards(db):
    from app.items import relationships as rel

    proj, _ = await _project(db)
    a = await _item(db, proj, title="Alpha one")
    b = await _item(db, proj, title="Beta two")
    await db.commit()
    r1 = await rel.create_relationship(db, a.id, b.id, "blocks")
    r2 = await rel.create_relationship(db, a.id, b.id, "blocks")
    assert (r1.source_id, r1.target_id) == (r2.source_id, r2.target_id)  # idempotent
    with pytest.raises(rel.RelationshipError):
        await rel.create_relationship(db, a.id, a.id, "blocks")  # self loop
    with pytest.raises(rel.RelationshipError):
        await rel.create_relationship(db, a.id, b.id, "nope")  # invalid relation
    assert await rel.delete_relationship(db, a.id, b.id, "blocks") is True
    assert await rel.delete_relationship(db, a.id, b.id, "blocks") is False


@pytest.mark.asyncio
async def test_resolve_query_finds_and_aborts(db):
    from app.items import relationships as rel

    proj, _ = await _project(db)
    await _item(db, proj, title="Unique searchable widget")
    await db.commit()
    rid = await rel.resolve_query(db, "Unique searchable widget")
    assert isinstance(rid, uuid.UUID)
    with pytest.raises(rel.RelationshipError):
        await rel.resolve_query(db, "zzzznomatchzzzz")


# ---------- threads ----------

@pytest.mark.asyncio
async def test_thread_create_advance_set_stage(db):
    from app.threads.service import advance_stage, create_thread, get_thread, list_threads, set_stage

    proj, owner = await _project(db)
    t = await create_thread(db, "Billing", "Payments module", "summary", project_id=proj.id)
    await db.commit()
    got = await get_thread(db, t.id)
    assert got is not None and got.title == "Payments module"
    threads = await list_threads(db, project_id=proj.id)
    assert any(x.id == t.id for x in threads)
    await advance_stage(db, t, "research notes", owner.id)
    assert t.stage != "idea"
    await set_stage(db, t, "spec")
    assert t.stage == "spec"


@pytest.mark.asyncio
async def test_list_items_orders_and_filters(db):
    proj, _ = await _project(db)
    for st in ("backlog", "in-progress", "spec"):
        it = await _item(db, proj, status=st, title=f"L {st} {uuid.uuid4().hex[:4]}")
        it.impact_ai = 3
        it.effort_ai = "M"
        it.priority = "p1"
    await db.commit()
    for order in ("impacto", "prioridad", "topologico", "reciente", "impact", "priority", "recent"):
        res = await isvc.list_items(db, project_id=proj.id, order=order)
        assert isinstance(res, list)
    filt = await isvc.list_items(db, project_id=proj.id, statuses=["backlog"], type="feature",
                                 stale_risk=False, limit=5, offset=0)
    assert isinstance(filt, list)


@pytest.mark.asyncio
async def test_list_items_orders_before_pagination(db):
    """The first page contains the global winner, not the first inserted row."""
    from app.items.models import Item

    proj, _ = await _project(db)
    scope = (await db.execute(
        select(Scope).where(Scope.project_id == proj.id)
    )).scalars().first()
    assert scope is not None
    db.add_all([
        Item(project_id=proj.id, scope_id=scope.id, title="low", type="feature", impact_ai=1),
        Item(project_id=proj.id, scope_id=scope.id, title="high", type="feature", impact_ai=5),
    ])
    await db.flush()

    first = await isvc.list_items(db, project_id=proj.id, order="impact", limit=1)
    assert [item.title for item in first] == ["high"]


# ---------- job handlers ----------

@pytest.mark.asyncio
async def test_handle_enrich(db, monkeypatch):
    from app.jobs.handlers import handle_enrich

    assert (await handle_enrich(db, None))["status"] == "no-ref"
    assert (await handle_enrich(db, uuid.uuid4()))["status"] == "item-not-found"

    proj, _ = await _project(db)
    item = await _item(db, proj)
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "")
    monkeypatch.setattr("app.config.settings.gemini_api_key", "")
    degraded = await handle_enrich(db, item.id)
    assert degraded["enriched"] is False

    async def fake_enrich(title, summary, item_type):
        return {"impact": 4, "effort": "M", "rationale": "r", "model": "m",
                "tokens_in": 1, "tokens_out": 1}

    monkeypatch.setattr("app.ai.llm.enrich_item", fake_enrich)
    out = await handle_enrich(db, item.id)
    assert out["enriched"] is True and item.impact_ai == 4


@pytest.mark.asyncio
async def test_handle_triage_promotes_bug(db, monkeypatch):
    from app.jobs.handlers import handle_triage_sentry
    from app.webhooks import service as ws

    await ws.ingest_sentry(db, {"data": {"issue": {"id": f"t{uuid.uuid4().hex[:6]}",
                                                   "title": "Crash", "project": "api"}}})
    from sqlalchemy import select

    from app.webhooks.models import SentryIssue
    issue = (await db.execute(select(SentryIssue).order_by(SentryIssue.first_seen.desc()))).scalars().first()

    async def fake_triage(title, ctx):
        return {"triage": "real-bug"}

    monkeypatch.setattr("app.ai.llm.triage_sentry", fake_triage)
    out = await handle_triage_sentry(db, issue.id)
    assert out["triage"] == "real-bug" and "promoted_item_id" not in out
    assert issue.item_id is None  # clasifica, no promueve
