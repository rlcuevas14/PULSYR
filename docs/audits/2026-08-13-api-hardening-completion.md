# API/backend hardening completion audit

Date: 2026-08-13

This audit maps the original five-lens framework to implemented controls and remaining
operator-owned evidence. “Implemented” means code and automated regression evidence
exist; it does not claim that an external monitor, staging VM or alert destination was
changed by this repository-only work.

| Lens / failure mode | Control and evidence | Owner | Metric | Runbook / residual action |
|---|---|---|---|---|
| Trust boundary: unauthenticated or cross-tenant access | Generated 117-operation inventory; session/token/admin/webhook/MCP authorization and tenant-isolation tests | App owner | 4xx by route | `docs/security/api-route-inventory.json`; review every route diff |
| Trust boundary: abuse and oversized work | Shared HMAC-keyed DB rate limits, trusted-proxy CIDRs, body/media ceilings | App owner | 413/415/429 by route | `observability-slo.md`; tune only from measured traffic |
| Data correctness: partial transactions | Rollback-on-error sessions, atomic queue capacity/dedup, backward-compatible migrations | App owner | 5xx and job errors | `staging-promotion-rollback.md`; validate every migration round-trip |
| Data correctness: cross-replica jobs | PostgreSQL advisory locks, row leases, SKIP LOCKED and cancellation requeue | App owner | jobs by state/age/outcome | `api-capacity-runbook.md#job-backlog` |
| Bounded work: DB exhaustion | Bounded pool acquisition, SQL timeout, pagination/search limits, compound indexes | Service owner | pool saturation and p95 | `api-capacity-runbook.md#database-pool` |
| Bounded work: queue growth | Per-project capacity and bounded worker concurrency | Service owner | pending count and oldest age | `api-capacity-runbook.md#job-backlog` |
| Visibility: uncorrelated/private telemetry | Request IDs, JSON logs, Sentry double scrubbing, route-template Prometheus labels | Service owner | RED plus Sentry events | `observability-slo.md`; periodically inspect scrubbing |
| Visibility: silent dependency failure | Bounded readiness query and metrics collection-success signal | Service owner | readiness probe and collection success | `observability-slo.md#triage` |
| Recovery: interrupted jobs | Cancellation returns the claimed row to pending; expired leases are reclaimed | Service owner | requeued outcome and oldest age | quarterly staging drill |
| Recovery: bad release | Container healthcheck, graceful shutdown budget, candidate readiness gate and prior-image rollback | Release owner | readiness and deploy result | `staging-promotion-rollback.md` |
| Recovery: stale abuse counters | Lifecycle-owned periodic prune with isolated error reporting | Service owner | maintenance Sentry event | verify row growth during monthly capacity review |
| Recovery: alerts not delivered | Versioned Prometheus rules and thresholds | Service owner | rule state | **Operator gap:** import rules, configure probe/contact and attach delivery evidence |
| Recovery: external drill not proven | Reproducible DB, worker and deploy-failure procedure | Release owner | drill evidence | **Operator gap:** execute against isolated staging and attach digests/timestamps |

## Phase evidence

1. Foundation and visibility: PR #31, merged as `5eeea0f`.
2. API/authentication and abuse resistance: PR #32, merged as `878e0a4`.
3. Data efficiency and scale: PR #33, merged as `0d425f8`; PostgreSQL benchmark
   reduced the representative impact query from 33.55 ms / 20,001 rows to
   0.33 ms / 50 rows.
4. Resilience and operations: this change; final PR/check evidence is recorded after
   CI completion.

## Automated failure evidence

- Full backend suite: 494 passed with 90.77% application coverage.
- Static gates: Ruff, mypy and the generated 117-operation route inventory pass.
- PostgreSQL migration drill: upgrade to `v0023`, downgrade to base, then upgrade to
  `v0023` completed against a disposable database.
- Real-process smoke: Uvicorn lifespan started with PostgreSQL; readiness returned 200,
  metrics returned 401 without the token and 200 with all seven signal families when
  authenticated.
- Database dependency failure produces bounded 503 readiness without exception detail.
- Durable-metric DB failure preserves the scrape and emits collection success `0`.
- Worker cancellation returns a running job to pending without a terminal result.
- Maintenance failure is reported and the loop remains cancellable.
- Deployment and compose contract tests require health, graceful-stop, bounded probes,
  previous-image capture and rollback instructions.

External staging and alert delivery remain explicitly operator-owned because this work
does not have staging infrastructure or monitoring-destination authority. They are
release gates, not silently assumed successes.
