# Phase 3 — API data efficiency and scale

Date: 2026-08-13

## Scope and decisions

- Move item impact, priority, and recent ordering into PostgreSQL before pagination.
- Bound REST collections to 200 rows, REST offsets to 10,000, MCP item/thread
  collections to 200, MCP search to 100, and topological candidates to 1,000.
- Scope MCP full-text search and UI thread aggregate queries in SQL, avoiding
  cross-project reads and post-query filtering.
- Add compound indexes matching tenant filters and deterministic sort keys.
- Serialize enqueue decisions in PostgreSQL, deduplicate active jobs by reference,
  cap active jobs per project, and run a bounded number of independent worker slots.
- Preserve Sentry events under backpressure: only optional asynchronous triage is
  skipped when capacity is exhausted.

## Measured query evidence

Local PostgreSQL 18, warm cache, 100,000 items, 10,000 threads, and 50,000 jobs.
The benchmark is intentionally much larger than the current production dataset.

| Query | Before/finding | Final | Final plan |
|---|---:|---:|---|
| Recent items, 50 | not tenant-indexed | 0.11 ms | compound index scan |
| Impact items, 50 | 33.55 ms / 20,001 rows scanned | 0.33 ms | index scan, 50 rows |
| Recent threads, 100 | no matching compound index | 0.09 ms | compound index scan |
| Claim oldest pending job | existing broad index | 0.17 ms | partial index scan |

The executable checker `scripts/check_query_budgets.py` requires each expected
index and an execution budget of 25 ms per query. CI-scale environments may use
`--max-scale` to relax elapsed time only; an unexpected query plan still fails.

## Operational budgets

- `JOB_CONCURRENCY=2` by default; range 1–16. Each active handler owns one DB session.
- `JOB_QUEUE_MAX_ACTIVE=1000` per project (and one separate legacy/global bucket).
- API collection response shape stays as a JSON array for compatibility.
- Offset pagination is retained for existing clients, but capped. Cursor pagination
  can be added as a future versioned API without changing this contract.

## Validation

- Alembic full `upgrade -> downgrade base -> upgrade` through v0023.
- Static analysis: Ruff and mypy.
- Targeted concurrency, deduplication, isolation, ordering, and boundary tests.
- A 32-request concurrent authenticated collection-read smoke test with a 5 s p95
  ceiling for noisy CI runners.
- Full application test suite and CI are required before merge.
