# API/backend hardening framework

Date: 2026-08-13

## Goal and operating method

Make the Pulsyr backend safer, efficient under normal and adversarial load,
horizontally scalable, and diagnosable without exposing customer data. Existing
controls are retained and strengthened; each phase must produce code, automated
evidence, operational documentation, and one reviewable pull request.

The review framework has five lenses:

1. **Trust boundaries:** authenticate callers, authorize tenant/resource access,
   validate untrusted input, and minimize exposed endpoints and sensitive output.
2. **Bounded work:** cap request size, query time, pool use, pagination, concurrency,
   retries, and queued work so overload fails predictably.
3. **Data correctness:** preserve tenant isolation, transaction integrity,
   idempotency, and safe lifecycle transitions.
4. **Operational visibility:** correlate requests, emit low-cardinality signals,
   measure latency/errors/saturation, and scrub private content.
5. **Recovery:** prove readiness, graceful shutdown, retry/lease behavior, rollback,
   alerts, and runbooks.

For every control: inventory current behavior, record the threat/failure mode, apply
the smallest compatible change, add a regression test, and capture the verification
command in the PR. A phase cannot merge with failing lint, type checks, tests,
migrations, or secret/dependency gates.

## Four implementation phases

| Phase | Scope | Exit evidence |
|---|---|---|
| 1. Foundation and visibility | Baseline audit; request correlation; privacy-safe JSON access logs; bounded PostgreSQL pool/query time; rollback and shutdown hygiene | Unit/HTTP tests prove IDs, safe log fields and numeric bounds; existing suite stays green |
| 2. API, authentication and abuse resistance | Machine-readable route inventory; explicit auth/authz classification; distributed rate limiting for login/OAuth/webhooks; proxy trust; body/content-type limits; consistent error envelope where compatible | Authorization matrix and abuse tests cover public, session, token, admin, webhook and MCP boundaries |
| 3. Data efficiency and scale | Query plans and hot-path profiling; indexes/N+1 removal; bounded pagination/search; worker concurrency/backpressure; idempotency; representative load test | Query/load budgets and migration round-trip pass with measured before/after evidence |
| 4. Resilience and operations | Metrics/traces for RED and saturation signals; job age/failure signals; graceful lifecycle; dependency degradation; alerts, dashboards, incident and capacity runbooks | Staging failure drills and final audit map every gap to a control, owner, metric and runbook |

Phase 2 starts only after Phase 1 is reviewed and merged; the same gate applies to
each following phase. Database schema changes remain backward-compatible so deploy
and rollback do not require simultaneous code/schema replacement.

