import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.mark.asyncio
async def test_enqueue_and_process_job(db: AsyncSession):
    from sqlalchemy import text

    from app.jobs.worker import enqueue_job, process_one

    # Aislar de jobs pendientes de otros tests (el worker procesa el más antiguo global).
    await db.execute(text("DELETE FROM agent_runs WHERE status = 'pending'"))
    run = await enqueue_job(db, kind="enrich", ref_type="item", ref_id=None)
    assert run.status == "pending"

    processed = await process_one(db)
    assert processed is True

    await db.refresh(run)
    assert run.status in ("ok", "error")
    assert run.finished_at is not None


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
