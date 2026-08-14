"""Bounded recurring database maintenance owned by the application lifecycle."""

import asyncio

from app.auth.rate_limit import rate_limiter
from app.config import settings
from app.database import SessionFactory
from app.observability import capture_exception


async def prune_rate_limits_once() -> None:
    async with SessionFactory() as db:
        await rate_limiter.prune(db)
        await db.commit()


async def maintenance_loop() -> None:
    while True:
        try:
            await prune_rate_limits_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            capture_exception(exc, component="database-maintenance", task="rate-limit-prune")
        await asyncio.sleep(settings.rate_limit_prune_interval_seconds)
