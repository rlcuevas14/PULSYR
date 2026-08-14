# Observability, SLO and incident runbook

## Runtime signals

- `/health/live`: process/ASGI liveness only; target for container orchestration.
- `/health/ready`: bounded critical database query; returns 503 without internal
  exception details when unavailable.
- Sentry: optional via `SENTRY_DSN`; environment comes from
  `DEPLOYMENT_ENVIRONMENT`, release from `RELEASE`, default PII is disabled and
  request bodies, cookies, query strings, identity and credential headers are scrubbed.
- Logs: every HTTP response includes `X-Request-ID`. A safe inbound ID is preserved;
  otherwise Pulsyr creates one. One JSON event per request records only method, route
  template, status and duration, avoiding query strings, bodies and identities.
  General log templates are emitted without interpolating runtime arguments in
  production; exception details remain in the privacy-scrubbed error tracker.
- Jobs: handler and worker-loop failures are captured with job kind/ID tags, never
  backlog content.

Use separate Sentry projects/DSNs for staging and production. Start with
`SENTRY_TRACES_SAMPLE_RATE=0.0`; increase only after reviewing data volume and privacy.

## Initial service objectives (rolling 30 days)

| Signal | Objective | Alert proposal | Owner |
|---|---:|---|---|
| Public landing availability | 99.9% | 2 failures from 3 regions in 5 min | Service owner |
| App login availability | 99.5% | 3 consecutive failures | Service owner |
| Readiness availability | 99.5% | 2 failures in 5 min | Service owner |
| Server 5xx ratio | <1% | >2% for 10 min | Service owner |
| API p95 latency | <750 ms | >1.5 s for 15 min | Service owner |
| Pending job oldest age | <10 min | >20 min | Service owner |

Uptime probes must not authenticate into or copy private backlog data. Configure the
alert destination and named backup contact in the operator's monitoring system; those
external writes were not performed by P5.

## Triage

1. Record alert time, environment, route, release/digest and request ID.
2. Check `/health/live`; if down, inspect process/container state. If live but not
   ready, inspect DB reachability, pool exhaustion and migrations.
3. Filter Sentry by environment/release and correlate request ID. Confirm scrubbing
   before sharing an event.
4. Inspect 5xx rate and job error/age. Pause non-critical workers if they amplify DB
   pressure.
5. Roll back the application digest using the staging runbook when the current
   release is causal and the schema is compatible.
6. After recovery, document impact, detection gap, timeline, corrective owner and due
   date. Test alert routing quarterly with a controlled staging exception.

## Database capacity guardrail

Each process may open at most `DB_POOL_SIZE + DB_MAX_OVERFLOW` PostgreSQL
connections. Keep the sum across all replicas below the database connection budget,
including migrations and operator access. Pool acquisition and SQL execution are
bounded by `DB_POOL_TIMEOUT_SECONDS` and `DB_STATEMENT_TIMEOUT_SECONDS`; increasing
either should follow evidence from route latency and database wait metrics.

## Abuse-control and proxy boundary

Password login, OAuth, MCP and webhooks use atomic PostgreSQL fixed-window
counters, so adding application replicas does not multiply the configured limits.
Counter keys are HMAC digests; raw client addresses and login identities are not
stored. Expired rows are safe to remove after `RATE_LIMIT_RETENTION_SECONDS`.

`CF-Connecting-IP` is ignored unless the direct ASGI peer belongs to
`TRUSTED_PROXY_CIDRS`. Configure only the internal Caddy/container network actually
connected to Uvicorn; never trust the internet as a whole. Body limits and media
types are enforced before authentication, JSON parsing, form parsing or database
work. Missing media types remain compatible with established signed senders, while
explicitly contradictory types are rejected. A `413`, `415` or `429` response includes the same request correlation ID as
the access event.
