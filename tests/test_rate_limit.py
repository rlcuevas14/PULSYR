from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from app.auth.rate_limit import DatabaseRateLimiter


@pytest.mark.asyncio
async def test_database_limiter_allows_through_limit_then_rejects(monkeypatch):
    limiter = DatabaseRateLimiter()
    db = AsyncMock()
    db.execute.side_effect = [Mock(scalar_one=Mock(return_value=value)) for value in (1, 2, 3)]

    first = await limiter.consume(db, bucket="test", key="hash", limit=2, window_seconds=60)
    second = await limiter.consume(db, bucket="test", key="hash", limit=2, window_seconds=60)
    third = await limiter.consume(db, bucket="test", key="hash", limit=2, window_seconds=60)

    assert first.allowed and second.allowed
    assert not third.allowed
    assert 1 <= third.retry_after <= 60
    sql = str(db.execute.call_args_list[0].args[0])
    assert "ON CONFLICT" in sql and "RETURNING attempts" in sql


@pytest.mark.asyncio
async def test_prune_removes_only_expired_windows(monkeypatch):
    limiter = DatabaseRateLimiter()
    db = AsyncMock()
    monkeypatch.setattr("app.auth.rate_limit.settings.rate_limit_retention_seconds", 3600)

    await limiter.prune(db)

    statement = db.execute.call_args.args[0]
    assert "DELETE FROM rate_limit_buckets" in str(statement)
    assert isinstance(datetime.now(timezone.utc), datetime)

