"""Low-cardinality Prometheus exposition without private application data."""

from __future__ import annotations

from collections import defaultdict
from threading import Lock

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import engine
from app.enums import AGENT_RUN_KINDS, AGENT_RUN_STATUSES
from app.jobs.models import AgentRun

_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
_lock = Lock()
_in_progress = 0
_requests: dict[tuple[str, str, str], int] = defaultdict(int)
_duration_buckets: dict[tuple[str, str], list[int]] = {}
_duration_sums: dict[tuple[str, str], float] = defaultdict(float)
_duration_counts: dict[tuple[str, str], int] = defaultdict(int)
_jobs: dict[tuple[str, str], int] = defaultdict(int)
_mcp_tools: dict[tuple[str, str], int] = defaultdict(int)


def request_started() -> None:
    global _in_progress
    with _lock:
        _in_progress += 1


def request_finished(method: str, route: str, status: int, duration_seconds: float) -> None:
    global _in_progress
    method = method if method in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"} else "OTHER"
    status_class = f"{status // 100}xx"
    key = (method, route)
    with _lock:
        _in_progress = max(0, _in_progress - 1)
        _requests[(method, route, status_class)] += 1
        counts = _duration_buckets.setdefault(key, [0] * len(_BUCKETS))
        for index, bucket in enumerate(_BUCKETS):
            if duration_seconds <= bucket:
                counts[index] += 1
        _duration_sums[key] += duration_seconds
        _duration_counts[key] += 1


def job_finished(kind: str, outcome: str) -> None:
    safe_kind = kind if kind in AGENT_RUN_KINDS else "unknown"
    safe_outcome = outcome if outcome in {"ok", "error", "requeued"} else "unknown"
    with _lock:
        _jobs[(safe_kind, safe_outcome)] += 1


def mcp_tool_finished(module: str, outcome: str) -> None:
    """Record only bounded MCP family/error labels; never tool arguments or entity ids."""
    safe_module = module if module in {"core", "threads", "incidents", "management"} else "unknown"
    safe_outcome = outcome if outcome in {
        "ok", "module_disabled", "write_scope_required", "not_found",
        "invalid_argument", "invalid_transition", "conflict",
        "integration_unavailable", "internal_error",
    } else "unknown"
    with _lock:
        _mcp_tools[(safe_module, safe_outcome)] += 1


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _sample(name: str, value: int | float, **labels: str) -> str:
    rendered = ",".join(f'{key}="{_escape(label)}"' for key, label in sorted(labels.items()))
    return f"{name}{{{rendered}}} {value}" if rendered else f"{name} {value}"


def _runtime_lines() -> list[str]:
    with _lock:
        requests = dict(_requests)
        duration_buckets = {key: tuple(values) for key, values in _duration_buckets.items()}
        duration_sums = dict(_duration_sums)
        duration_counts = dict(_duration_counts)
        jobs = dict(_jobs)
        mcp_tools = dict(_mcp_tools)
        in_progress = _in_progress

    lines = [
        "# HELP pulsyr_http_requests_total Completed HTTP requests.",
        "# TYPE pulsyr_http_requests_total counter",
    ]
    for (method, route, status_class), count in sorted(requests.items()):
        lines.append(_sample(
            "pulsyr_http_requests_total", count,
            method=method, route=route, status_class=status_class,
        ))
    lines.extend([
        "# HELP pulsyr_http_request_duration_seconds HTTP request duration.",
        "# TYPE pulsyr_http_request_duration_seconds histogram",
    ])
    for (method, route), values in sorted(duration_buckets.items()):
        for bucket, count in zip(_BUCKETS, values, strict=True):
            lines.append(_sample(
                "pulsyr_http_request_duration_seconds_bucket",
                count,
                method=method, route=route, le=str(bucket),
            ))
        lines.append(_sample(
            "pulsyr_http_request_duration_seconds_bucket", duration_counts[(method, route)],
            method=method, route=route, le="+Inf",
        ))
        lines.append(_sample(
            "pulsyr_http_request_duration_seconds_sum", duration_sums[(method, route)],
            method=method, route=route,
        ))
        lines.append(_sample(
            "pulsyr_http_request_duration_seconds_count", duration_counts[(method, route)],
            method=method, route=route,
        ))
    lines.extend([
        "# HELP pulsyr_http_requests_in_progress Requests currently executing.",
        "# TYPE pulsyr_http_requests_in_progress gauge",
        _sample("pulsyr_http_requests_in_progress", in_progress),
        "# HELP pulsyr_jobs_processed_total Worker outcomes since process start.",
        "# TYPE pulsyr_jobs_processed_total counter",
    ])
    for (kind, outcome), count in sorted(jobs.items()):
        lines.append(_sample("pulsyr_jobs_processed_total", count, kind=kind, outcome=outcome))
    lines.extend([
        "# HELP pulsyr_mcp_tool_calls_total MCP tool calls by capability family and outcome.",
        "# TYPE pulsyr_mcp_tool_calls_total counter",
    ])
    for (module, outcome), count in sorted(mcp_tools.items()):
        lines.append(_sample(
            "pulsyr_mcp_tool_calls_total", count, module=module, outcome=outcome
        ))
    return lines


async def render_metrics(db: AsyncSession) -> str:
    """Render process, pool and durable queue signals; degrade if DB collection fails."""
    lines = _runtime_lines()
    pool = engine.pool
    pool_values = {
        "configured_capacity": settings.db_pool_size + settings.db_max_overflow,
        "checked_in": getattr(pool, "checkedin", lambda: 0)(),
        "checked_out": getattr(pool, "checkedout", lambda: 0)(),
    }
    lines.extend([
        "# HELP pulsyr_db_pool_connections Database pool connections by state.",
        "# TYPE pulsyr_db_pool_connections gauge",
    ])
    for state, value in pool_values.items():
        lines.append(_sample("pulsyr_db_pool_connections", value, state=state))

    try:
        rows = (await db.execute(
            select(AgentRun.status, func.count()).group_by(AgentRun.status)
        )).all()
        counts = {status: int(count) for status, count in rows}
        oldest = await db.scalar(
            select(func.extract("epoch", func.now() - func.min(AgentRun.created_at))).where(
                AgentRun.status == "pending"
            )
        )
        lines.extend([
            "# HELP pulsyr_jobs_by_status Durable job rows by state.",
            "# TYPE pulsyr_jobs_by_status gauge",
        ])
        for status in AGENT_RUN_STATUSES:
            lines.append(_sample("pulsyr_jobs_by_status", counts.get(status, 0), status=status))
        lines.extend([
            "# HELP pulsyr_job_oldest_pending_age_seconds Age of the oldest pending job.",
            "# TYPE pulsyr_job_oldest_pending_age_seconds gauge",
            _sample("pulsyr_job_oldest_pending_age_seconds", float(oldest or 0)),
            "# HELP pulsyr_metrics_collection_success Whether durable metrics were collected.",
            "# TYPE pulsyr_metrics_collection_success gauge",
            _sample("pulsyr_metrics_collection_success", 1),
        ])
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        lines.extend([
            "# HELP pulsyr_metrics_collection_success Whether durable metrics were collected.",
            "# TYPE pulsyr_metrics_collection_success gauge",
        ])
        lines.append(_sample("pulsyr_metrics_collection_success", 0))
    return "\n".join(lines) + "\n"


def reset_metrics_for_tests() -> None:
    global _in_progress
    with _lock:
        _in_progress = 0
        _requests.clear()
        _duration_buckets.clear()
        _duration_sums.clear()
        _duration_counts.clear()
        _jobs.clear()
        _mcp_tools.clear()
