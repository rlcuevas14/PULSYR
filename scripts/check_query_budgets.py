"""Verify that representative hot-path queries keep their index and time budgets."""

import argparse
import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@dataclass(frozen=True)
class QueryBudget:
    name: str
    sql: str
    expected_index: str
    max_ms: float


QUERIES = (
    QueryBudget(
        "items_recent",
        "SELECT id, title FROM items WHERE project_id = :project_id "
        "ORDER BY created_at DESC, id LIMIT 50",
        "items_project_created_id_idx",
        25.0,
    ),
    QueryBudget(
        "items_impact",
        "SELECT id, title FROM items WHERE project_id = :project_id "
        "ORDER BY impact_ai DESC NULLS LAST, effort_ai ASC NULLS LAST, id LIMIT 50",
        "items_project_impact_id_idx",
        25.0,
    ),
    QueryBudget(
        "threads_recent",
        "SELECT id, title FROM threads WHERE project_id = :project_id "
        "ORDER BY updated_at DESC, id LIMIT 100",
        "threads_project_updated_id_idx",
        25.0,
    ),
    QueryBudget(
        "job_claim",
        "SELECT id FROM agent_runs WHERE status = 'pending' "
        "ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED",
        "agent_runs_pending_created_id_idx",
        25.0,
    ),
)


def _walk(plan: dict[str, Any]):
    yield plan
    for child in plan.get("Plans", []):
        yield from _walk(child)


async def check(database_url: str, project_id: uuid.UUID, max_scale: float) -> list[dict[str, Any]]:
    engine = create_async_engine(database_url)
    failures: list[str] = []
    results: list[dict[str, Any]] = []
    try:
        async with engine.begin() as conn:
            for budget in QUERIES:
                raw = await conn.execute(
                    text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {budget.sql}"),
                    {"project_id": project_id},
                )
                document = raw.scalar_one()
                report = document[0] if isinstance(document, list) else json.loads(document)[0]
                plan = report["Plan"]
                indexes = {
                    node["Index Name"] for node in _walk(plan) if node.get("Index Name")
                }
                elapsed = float(report["Execution Time"])
                allowed = budget.max_ms * max_scale
                results.append({
                    "name": budget.name,
                    "execution_ms": elapsed,
                    "max_ms": allowed,
                    "indexes": sorted(indexes),
                })
                if budget.expected_index not in indexes:
                    failures.append(f"{budget.name}: missing {budget.expected_index}")
                if elapsed > allowed:
                    failures.append(f"{budget.name}: {elapsed:.2f} ms exceeds {allowed:.2f} ms")
    finally:
        await engine.dispose()
    if failures:
        raise RuntimeError("; ".join(failures))
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--project-id", required=True)
    parser.add_argument(
        "--max-scale", type=float, default=1.0,
        help="Multiplier for noisy shared runners; expected indexes remain mandatory.",
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    print(json.dumps(
        asyncio.run(check(args.database_url, uuid.UUID(args.project_id), args.max_scale)),
        indent=2,
    ))


if __name__ == "__main__":
    main()
