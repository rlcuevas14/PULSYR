# API capacity and saturation runbook

This runbook turns Pulsyr's bounded runtime controls into scaling decisions. Metrics
contain route templates and closed enums only; never add account, user, project,
item, request path, query string or job ID labels.

## Request latency

Start with the p50/p95/p99 histogram by route and compare it with request volume and
5xx ratio. A slow route with stable volume points to its query plan or dependency; a
global rise with increasing in-progress requests points to shared capacity.

1. Correlate the first affected release and route with privacy-safe request logs.
2. Check database pool usage and PostgreSQL waits before raising any timeout.
3. Confirm pagination and query-budget guards still pass. Profile with representative
   tenant data, never production content copied into a developer environment.
4. Roll back the application when the release is causal and schema-compatible.

Scale application replicas only while the aggregate configured pool capacity remains
inside PostgreSQL's connection budget. More replicas can make a database bottleneck
worse.

## Database pool

`pulsyr_db_pool_connections{state="checked_out"}` divided by
`state="configured_capacity"` is the per-process saturation ratio. Sustained values
above 85% require investigation; transient peaks are expected.

1. Check p95 latency, statement timeouts and PostgreSQL active/waiting sessions.
2. Identify whether web traffic or job handlers own the pressure. Each worker slot
   holds a session while its handler runs.
3. Reduce `JOB_CONCURRENCY` first if background work is amplifying an incident.
4. Increase pool or replica counts only after calculating:
   `(DB_POOL_SIZE + DB_MAX_OVERFLOW) * app_processes + migration/operator reserve`.
5. Keep the result below the database limit with at least 20% emergency reserve.

## Job backlog

Use `pulsyr_jobs_by_status`, `pulsyr_job_oldest_pending_age_seconds` and the error rate
from `pulsyr_jobs_processed_total` together.

- Old age with no errors: workers are absent, undersized or blocked on a dependency.
- Rising errors: inspect Sentry by job kind and release; do not log job payloads.
- Rising pending and pool saturation: lower web/worker contention before adding slots.
- Running jobs older than `JOB_LEASE_SECONDS`: verify clock health and lease reclaim.

The queue rejects new work at `JOB_QUEUE_MAX_ACTIVE`; increasing that bound delays
failure and consumes storage but does not increase throughput. Add capacity only after
measuring average handler time and downstream limits.

## Capacity review cadence

Monthly and before a traffic-changing launch, record peak request rate, p95, 5xx ratio,
pool saturation, oldest pending job, worker error rate, database connection budget and
headroom. Assign a named owner and a date to any signal above 70% of its bound. Exercise
DB-unavailable readiness, worker cancellation/requeue and candidate rollback in staging
quarterly; attach timestamps and alert-delivery evidence to the release record.
