import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionFactory
from app.jobs.handlers import HANDLERS
from app.jobs.models import AgentRun


async def enqueue_job(
    db: AsyncSession,
    kind: str,
    ref_type: str | None = None,
    ref_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> AgentRun:
    run = AgentRun(
        kind=kind, ref_type=ref_type, ref_id=ref_id, status="pending", project_id=project_id
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


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
        .order_by(AgentRun.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    run = result.scalar_one_or_none()
    if run is None:
        return False

    run.status = "running"
    run.leased_until = lease_until
    await db.commit()

    handler = HANDLERS.get(run.kind)
    try:
        if handler:
            result_data = await handler(db, run.ref_id)
        else:
            result_data = {"warning": f"No handler for kind='{run.kind}'"}

        run.status = "ok"
        run.result = result_data
    except Exception as exc:
        run.status = "error"
        run.error = str(exc)
    finally:
        run.finished_at = datetime.now(timezone.utc)
        run.leased_until = None
        await db.commit()

    return True


async def worker_loop() -> None:
    """Asyncio loop that runs in the app's lifespan. Never raises."""
    poll_interval = settings.job_poll_interval_seconds
    async with SessionFactory() as db:
        while True:
            try:
                await reclaim_expired_leases(db)
                processed = await process_one(db)
                if not processed:
                    await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(poll_interval)
