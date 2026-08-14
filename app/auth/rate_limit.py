"""Database-backed fixed-window abuse controls shared by every app replica."""

import hashlib
import hmac
import ipaddress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after: int


def _trusted_proxy_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    return tuple(
        ipaddress.ip_network(raw.strip(), strict=False)
        for raw in settings.trusted_proxy_cidrs.split(",")
        if raw.strip()
    )


def client_address(request: Request) -> str:
    """Use a forwarded address only when the direct peer is explicitly trusted."""
    peer = request.client.host if request.client else "unknown"
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if not any(peer_ip in network for network in _trusted_proxy_networks()):
        return peer

    forwarded = request.headers.get("cf-connecting-ip", "").strip()
    try:
        return str(ipaddress.ip_address(forwarded)) if forwarded else peer
    except ValueError:
        return peer


def privacy_key(action: str, value: str) -> str:
    return hmac.new(
        settings.secret_key.encode(),
        f"{action}:{value}".encode(),
        hashlib.sha256,
    ).hexdigest()


class DatabaseRateLimiter:
    async def consume(
        self,
        db: AsyncSession,
        *,
        bucket: str,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        """Atomically increment one bucket without committing the caller transaction."""
        now = datetime.now(timezone.utc)
        window_start = now.replace(microsecond=0) - timedelta(
            seconds=int(now.timestamp()) % window_seconds
        )
        result = await db.execute(
            text(
                """
                INSERT INTO rate_limit_buckets (bucket, key_hash, window_start, attempts)
                VALUES (:bucket, :key_hash, :window_start, 1)
                ON CONFLICT (bucket, key_hash, window_start)
                DO UPDATE SET attempts = rate_limit_buckets.attempts + 1
                RETURNING attempts
                """
            ),
            {"bucket": bucket, "key_hash": key, "window_start": window_start},
        )
        attempts = int(result.scalar_one())
        retry_after = max(1, window_seconds - (int(now.timestamp()) % window_seconds))
        return RateLimitDecision(allowed=attempts <= limit, retry_after=retry_after)

    async def prune(self, db: AsyncSession) -> None:
        from app.auth.models import RateLimitBucket

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.rate_limit_retention_seconds)
        await db.execute(delete(RateLimitBucket).where(RateLimitBucket.window_start < cutoff))


rate_limiter = DatabaseRateLimiter()


async def limit_client(
    request: Request,
    *,
    action: str,
    limit: int,
    window_seconds: int,
) -> RateLimitDecision:
    from app.database import SessionFactory

    async with SessionFactory() as db:
        decision = await rate_limiter.consume(
            db,
            bucket=action,
            key=privacy_key(action, client_address(request)),
            limit=limit,
            window_seconds=window_seconds,
        )
        await db.commit()
        return decision


async def limit_value(
    *,
    action: str,
    value: str,
    limit: int,
    window_seconds: int,
) -> RateLimitDecision:
    from app.database import SessionFactory

    async with SessionFactory() as db:
        decision = await rate_limiter.consume(
            db,
            bucket=action,
            key=privacy_key(action, value.strip().casefold()),
            limit=limit,
            window_seconds=window_seconds,
        )
        await db.commit()
        return decision


class _LegacyTestReset:
    """Keep the old test seam while production counters remain database-backed."""

    def reset(self) -> None:
        return None


oauth_rate_limiter = _LegacyTestReset()
