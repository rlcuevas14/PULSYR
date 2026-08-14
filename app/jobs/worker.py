import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionFactory
from app.jobs.handlers import HANDLERS
from app.jobs.models import AgentRun
from app.metrics import job_finished
from app.observability import capture_exception


class JobQueueFull(RuntimeError):
    """The bounded active queue cannot accept another job."""


def _project_filter(project_id: uuid.UUID | None):
    if project_id is None:
        return AgentRun.project_id.is_(None)
    return AgentRun.project_id == project_id


async def _lock_queue(db: AsyncSession, project_id: uuid.UUID | None) -> None:
    lock_key = str(project_id) if project_id is not None else "global"
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"pulsyr-job-queue:{lock_key}"},
    )


async def _active_count(db: AsyncSession, project_id: uuid.UUID | None) -> int:
    active_ids = (
        select(AgentRun.id).where(
            _project_filter(project_id), AgentRun.status.in_(["pending", "running"])
        ).limit(settings.job_queue_max_active).subquery()
    )
    return int(await db.scalar(select(func.count()).select_from(active_ids)) or 0)


async def enqueue_job(
    db: AsyncSession,
    kind: str,
    ref_type: str | None = None,
    ref_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    *,
    commit: bool = True,
) -> AgentRun:
    # Serialize enqueue decisions per project. PostgreSQL holds the transaction
    # lock through the check and insert, so capacity and dedup work across replicas.
    await _lock_queue(db, project_id)

    if ref_id is not None:
        existing = await db.scalar(
            select(AgentRun).where(
                _project_filter(project_id),
                AgentRun.kind == kind,
                AgentRun.ref_type == ref_type,
                AgentRun.ref_id == ref_id,
                AgentRun.status.in_(["pending", "running"]),
            )
        )
        if existing is not None:
            if commit:
                await db.commit()
            return existing

    if await _active_count(db, project_id) >= settings.job_queue_max_active:
        if commit:
            await db.rollback()
        raise JobQueueFull(
            f"active job queue reached its capacity ({settings.job_queue_max_active})"
        )

    run = AgentRun(
        kind=kind, ref_type=ref_type, ref_id=ref_id, status="pending", project_id=project_id
    )
    if commit:
        db.add(run)
        await db.commit()
        await db.refresh(run)
        return run

    db.add(run)
    await db.flush()
    return run


async def enqueue_jobs(
    db: AsyncSession,
    kind: str,
    ref_type: str,
    ref_ids: list[uuid.UUID],
    project_id: uuid.UUID | None,
) -> tuple[int, int, bool]:
    """Enqueue unique references in one transaction.

    Returns ``(queued, already_active, capacity_reached)``.
    """
    unique_ids = list(dict.fromkeys(ref_ids))
    if not unique_ids:
        return 0, 0, False

    await _lock_queue(db, project_id)
    existing_rows = await db.scalars(
        select(AgentRun.ref_id).where(
            _project_filter(project_id),
            AgentRun.kind == kind,
            AgentRun.ref_type == ref_type,
            AgentRun.ref_id.in_(unique_ids),
            AgentRun.status.in_(["pending", "running"]),
        )
    )
    existing = {ref_id for ref_id in existing_rows if ref_id is not None}
    candidates = [ref_id for ref_id in unique_ids if ref_id not in existing]
    available = max(settings.job_queue_max_active - await _active_count(db, project_id), 0)
    accepted = candidates[:available]
    db.add_all([
        AgentRun(
            kind=kind, ref_type=ref_type, ref_id=ref_id,
            status="pending", project_id=project_id,
        )
        for ref_id in accepted
    ])
    await db.commit()
    return len(accepted), len(existing), len(accepted) < len(candidates)


async def reclaim_expired_leases(db: AsyncSession) -> int:
    """Return jobs whose lease has expired back to 'pending'."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(AgentRun)
        .where(AgentRun.status == "running", AgentRun.leased_until < now)
        .values(status="pending", leased_until=None)
    )
    await db.commit()
    return result.rowcount  # type: ignore[attr-defined]


async def process_one(db: AsyncSession) -> bool:
    """Pick up and process one job. Returns True if something was processed."""
    now = datetime.now(timezone.utc)
    lease_until = now + timedelta(seconds=settings.job_lease_seconds)

    result = await db.execute(
        select(AgentRun)
        .where(AgentRun.status == "pending")
        .order_by(AgentRun.created_at, AgentRun.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    run = result.scalar_one_or_none()
    if run is None:
        return False

    run.status = "running"
    run.leased_until = lease_until
    run.finished_at = None
    run.error = None
    await db.commit()

    handler = HANDLERS.get(run.kind)
    run_id = run.id
    run_kind = run.kind
    try:
        if handler:
            result_data = await handler(db, run.ref_id)
        else:
            result_data = {"warning": f"No handler for kind='{run.kind}'"}
    except asyncio.CancelledError:
        # Roll back partial handler writes, then make the claimed job immediately
        # available to another process during a graceful restart.
        await db.rollback()
        cancelled_run = await db.get(AgentRun, run_id)
        if cancelled_run is not None and cancelled_run.status == "running":
            cancelled_run.status = "pending"
            cancelled_run.leased_until = None
            await db.commit()
        job_finished(run_kind, "requeued")
        raise
    except Exception as exc:
        # A database exception may have invalidated the handler transaction. Roll
        # it back before recording the terminal job state in a clean transaction.
        await db.rollback()
        failed_run = await db.get(AgentRun, run_id)
        if failed_run is not None:
            failed_run.status = "error"
            failed_run.error = str(exc)
            failed_run.finished_at = datetime.now(timezone.utc)
            failed_run.leased_until = None
            await db.commit()
        capture_exception(exc, component="job-worker", job_kind=run_kind, job_id=str(run_id))
        job_finished(run_kind, "error")
        return True

    run.status = "ok"
    run.result = result_data
    run.finished_at = datetime.now(timezone.utc)
    run.leased_until = None
    await db.commit()
    job_finished(run_kind, "ok")

    return True


async def _worker_slot(slot: int) -> None:
    """Process jobs sequentially inside one bounded-concurrency slot."""
    poll_interval = settings.job_poll_interval_seconds
    reclaim_interval = min(max(poll_interval, 1), max(settings.job_lease_seconds // 3, 1))
    next_reclaim = 0.0
    loop = asyncio.get_running_loop()
    async with SessionFactory() as db:
        while True:
            try:
                now = loop.time()
                if slot == 0 and now >= next_reclaim:
                    await reclaim_expired_leases(db)
                    next_reclaim = now + reclaim_interval
                processed = await process_one(db)
                if not processed:
                    await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await db.rollback()
                capture_exception(exc, component="job-worker-loop", worker_slot=str(slot))
                await asyncio.sleep(poll_interval)


async def worker_loop() -> None:
    """Run a configured, bounded number of independent worker slots."""
    tasks = [
        asyncio.create_task(_worker_slot(slot), name=f"pulsyr-worker-{slot}")
        for slot in range(settings.job_concurrency)
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
