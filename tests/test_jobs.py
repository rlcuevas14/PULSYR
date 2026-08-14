import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.mark.asyncio
async def test_enqueue_and_process_job(db: AsyncSession, monkeypatch):
    from sqlalchemy import text

    from app.jobs import worker

    metric = Mock()
    monkeypatch.setattr(worker, "job_finished", metric)

    # Aislar de jobs pendientes de otros tests (el worker procesa el más antiguo global).
    await db.execute(text("DELETE FROM agent_runs WHERE status = 'pending'"))
    run = await worker.enqueue_job(db, kind="enrich", ref_type="item", ref_id=None)
    assert run.status == "pending"

    processed = await worker.process_one(db)
    assert processed is True

    await db.refresh(run)
    assert run.status in ("ok", "error")
    assert run.finished_at is not None
    metric.assert_called_once_with("enrich", run.status)


@pytest.mark.asyncio
async def test_worker_failure_records_terminal_metric(db: AsyncSession, monkeypatch):
    from sqlalchemy import text

    from app.jobs import worker
    from app.jobs.handlers import HANDLERS

    await db.execute(text("DELETE FROM agent_runs WHERE status IN ('pending','running')"))
    await db.commit()
    run = await worker.enqueue_job(db, "enrich")

    async def fail(_db, _ref_id):
        raise RuntimeError("controlled")

    metric = Mock()
    monkeypatch.setitem(HANDLERS, "enrich", fail)
    monkeypatch.setattr(worker, "job_finished", metric)

    assert await worker.process_one(db) is True
    await db.refresh(run)
    assert run.status == "error"
    metric.assert_called_once_with("enrich", "error")


@pytest.mark.asyncio
async def test_no_double_processing(db: AsyncSession, test_engine):
    """Dos corrutinas concurrentes no deben procesar el mismo job."""
    from sqlalchemy import text

    from app.jobs.worker import enqueue_job, process_one

    # Aislar de jobs pendientes que otros tests pudieran haber dejado (el worker es global).
    await db.execute(text("DELETE FROM agent_runs WHERE status = 'pending'"))
    await enqueue_job(db, kind="enrich")
    await db.commit()

    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)
    async with TestSession() as db1, TestSession() as db2:
        results = await asyncio.gather(process_one(db1), process_one(db2))
    assert results.count(True) == 1
    assert results.count(False) == 1


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed(db: AsyncSession):
    """Un job con lease expirada debe poder ser retomado."""
    from app.jobs.worker import enqueue_job, reclaim_expired_leases

    run = await enqueue_job(db, kind="enrich")
    run.status = "running"
    run.leased_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db.commit()

    await reclaim_expired_leases(db)
    await db.refresh(run)
    assert run.status == "pending"
    assert run.leased_until is None


@pytest.mark.asyncio
async def test_process_one_returns_false_when_queue_empty(db: AsyncSession):
    from sqlalchemy import text

    from app.jobs.worker import process_one

    # Asegurar que no queden jobs pendientes de tests anteriores.
    await db.execute(text("DELETE FROM agent_runs WHERE status = 'pending'"))
    await db.commit()

    processed = await process_one(db)
    assert processed is False


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_for_active_reference(db: AsyncSession):
    from app.jobs.models import AgentRun
    from app.jobs.worker import enqueue_job

    ref_id = uuid.uuid4()
    first = await enqueue_job(db, "enrich", "item", ref_id)
    second = await enqueue_job(db, "enrich", "item", ref_id)

    assert second.id == first.id
    count = await db.scalar(
        select(func.count()).select_from(AgentRun).where(
            AgentRun.kind == "enrich", AgentRun.ref_id == ref_id
        )
    )
    assert count == 1


@pytest.mark.asyncio
async def test_enqueue_applies_queue_capacity(db: AsyncSession, monkeypatch):
    from sqlalchemy import text

    from app.config import settings
    from app.jobs.worker import JobQueueFull, enqueue_job

    await db.execute(text("DELETE FROM agent_runs WHERE status IN ('pending','running')"))
    await db.commit()
    monkeypatch.setattr(settings, "job_queue_max_active", 1)
    await enqueue_job(db, "enrich")
    with pytest.raises(JobQueueFull):
        await enqueue_job(db, "enrich")


@pytest.mark.asyncio
async def test_bulk_enqueue_deduplicates_and_commits_once(db: AsyncSession):
    from sqlalchemy import text

    from app.jobs.worker import enqueue_jobs

    await db.execute(text("DELETE FROM agent_runs WHERE status IN ('pending','running')"))
    await db.commit()
    refs = [uuid.uuid4(), uuid.uuid4()]

    assert await enqueue_jobs(db, "enrich", "item", refs, None) == (2, 0, False)
    assert await enqueue_jobs(db, "enrich", "item", refs, None) == (0, 2, False)


@pytest.mark.asyncio
async def test_two_worker_sessions_process_jobs_concurrently(db, test_engine, monkeypatch):
    from sqlalchemy import text

    from app.jobs.handlers import HANDLERS
    from app.jobs.worker import enqueue_job, process_one

    await db.execute(text("DELETE FROM agent_runs WHERE status IN ('pending','running')"))
    await db.commit()
    await enqueue_job(db, "enrich")
    await enqueue_job(db, "enrich")

    active = 0
    maximum = 0
    both_started = asyncio.Event()

    async def concurrent_handler(_db, _ref_id):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        if active == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        active -= 1
        return {"ok": True}

    monkeypatch.setitem(HANDLERS, "enrich", concurrent_handler)
    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)
    async with TestSession() as first, TestSession() as second:
        assert await asyncio.gather(process_one(first), process_one(second)) == [True, True]
    assert maximum == 2


@pytest.mark.asyncio
async def test_cancelled_job_returns_to_pending(db, test_engine, monkeypatch):
    from sqlalchemy import text

    from app.jobs import worker
    from app.jobs.handlers import HANDLERS
    from app.jobs.models import AgentRun

    await db.execute(text("DELETE FROM agent_runs WHERE status IN ('pending','running')"))
    await db.commit()
    run = await worker.enqueue_job(db, "enrich")
    started = asyncio.Event()

    async def waiting_handler(_db, _ref_id):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setitem(HANDLERS, "enrich", waiting_handler)
    metric = Mock()
    monkeypatch.setattr(worker, "job_finished", metric)
    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)
    async with TestSession() as worker_db:
        task = asyncio.create_task(worker.process_one(worker_db))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async with TestSession() as verify_db:
        persisted = await verify_db.get(AgentRun, run.id)
        assert persisted is not None
        assert persisted.status == "pending"
        assert persisted.leased_until is None
        assert persisted.finished_at is None
    metric.assert_called_once_with("enrich", "requeued")
