import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app import maintenance, metrics
from app.jobs.models import AgentRun
from app.main import create_app


@pytest.fixture(autouse=True)
def clean_metrics():
    metrics.reset_metrics_for_tests()
    yield
    metrics.reset_metrics_for_tests()


@pytest.mark.asyncio
async def test_metrics_are_disabled_without_a_token(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "metrics_bearer_token", "")
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.get("/metrics")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_metrics_require_auth_and_expose_red_pool_and_queue_signals(
    client, db, monkeypatch
):
    from app.config import settings

    token = "m" * 32
    monkeypatch.setattr(settings, "metrics_bearer_token", token)
    await db.execute(delete(AgentRun).where(AgentRun.status == "pending"))
    db.add(AgentRun(
        kind="enrich",
        status="pending",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=3),
    ))
    await db.commit()

    await client.get("/health/live?private=secret")
    unauthorized = await client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    response = await client.get("/metrics", headers={"Authorization": f"Bearer {token}"})

    assert unauthorized.status_code == 401
    assert unauthorized.headers["www-authenticate"] == "Bearer"
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert (
        'pulsyr_http_requests_total{method="GET",route="/health/live",status_class="2xx"} 1'
        in response.text
    )
    assert 'pulsyr_jobs_by_status{status="pending"} 1' in response.text
    assert "pulsyr_job_oldest_pending_age_seconds" in response.text
    assert 'pulsyr_db_pool_connections{state="checked_out"}' in response.text
    assert "pulsyr_metrics_collection_success 1" in response.text
    assert "private" not in response.text
    assert token not in response.text


@pytest.mark.asyncio
async def test_metrics_degrade_when_queue_collection_fails():
    db = AsyncMock()
    db.execute.side_effect = RuntimeError("database unavailable")

    payload = await metrics.render_metrics(db)

    assert "pulsyr_metrics_collection_success 0" in payload
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_metrics_still_degrade_when_failed_connection_cannot_rollback():
    db = AsyncMock()
    db.execute.side_effect = RuntimeError("database unavailable")
    db.rollback.side_effect = RuntimeError("connection closed")

    payload = await metrics.render_metrics(db)

    assert "pulsyr_metrics_collection_success 0" in payload


def test_metric_labels_and_job_values_are_bounded_and_escaped():
    metrics.request_started()
    metrics.request_finished("CUSTOM", '/unknown/"route', 599, 0.02)
    metrics.job_finished("private-kind", "private-outcome")

    payload = "\n".join(metrics._runtime_lines())

    assert 'method="OTHER"' in payload
    assert 'route="/unknown/\\"route"' in payload
    assert 'kind="unknown",outcome="unknown"' in payload
    assert "pulsyr_http_requests_in_progress 0" in payload


def test_histogram_storage_is_constant_per_route():
    for _ in range(10_000):
        metrics.request_started()
        metrics.request_finished("GET", "/health/live", 200, 0.02)

    assert len(metrics._duration_buckets) == 1
    assert len(metrics._duration_buckets[("GET", "/health/live")]) == len(metrics._BUCKETS)
    assert metrics._duration_counts[("GET", "/health/live")] == 10_000


@pytest.mark.asyncio
async def test_rate_limit_maintenance_commits(monkeypatch):
    session = AsyncMock()

    class Context:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_args):
            return None

    prune = AsyncMock()
    monkeypatch.setattr(maintenance, "SessionFactory", lambda: Context())
    monkeypatch.setattr(maintenance.rate_limiter, "prune", prune)

    await maintenance.prune_rate_limits_once()

    prune.assert_awaited_once_with(session)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_maintenance_reports_failure_then_remains_cancellable(monkeypatch):
    prune = AsyncMock(side_effect=RuntimeError("controlled"))
    capture = Mock()
    sleep = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(maintenance, "prune_rate_limits_once", prune)
    monkeypatch.setattr(maintenance, "capture_exception", capture)
    monkeypatch.setattr(maintenance.asyncio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        await maintenance.maintenance_loop()

    capture.assert_called_once()
    assert capture.call_args.kwargs == {
        "component": "database-maintenance",
        "task": "rate-limit-prune",
    }
