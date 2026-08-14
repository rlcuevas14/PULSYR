# API/backend hardening baseline

Date: 2026-08-13
Revision audited: `origin/main` at `0ab0f2ff83460dea1d1e85d2417d7f7c1a020da8`

## Current strengths

- Production refuses placeholder or short session secrets; interactive API docs are
  disabled outside debug mode.
- Session cookies are secure, strict SameSite and time bounded; browser mutations
  use double-submit CSRF protection and responses carry security headers.
- API-token and session flows scope data access by project/account, with regression
  coverage for cross-tenant denial.
- Liveness and bounded database readiness are separate; unexpected errors return
  generic content and Sentry strips bodies, cookies, query strings and identity.
- Background jobs use leases and `SKIP LOCKED`, providing a sound multi-worker base.

## Endpoint surface

The application exposes six distinct trust classes that Phase 2 must turn into a
generated authorization contract:

| Class | Representative surfaces | Primary control today |
|---|---|---|
| Public | health, login/signup, OAuth callbacks, setup | route-level policy and signup configuration |
| Browser session | project/account/admin and `/ui/*` mutations | session dependency plus CSRF |
| API token | `/api/v1/items`, scopes, threads | token project scope and role checks |
| Webhook | GitHub and Sentry receivers | shared secret/signature validation |
| MCP | `/mcp` JSON-RPC tools | API token and project scope |
| Static/private UI | `/static`, HTML routes | private-origin noindex and response policy |

## Gaps and disposition

| Risk | Baseline observation | Severity | Phase |
|---|---|---:|---:|
| Requests cannot be followed across successful calls | IDs were generated only inside error handlers and were not shared with access logs | High | 1 |
| Logs are difficult to aggregate safely | Plain-text format and raw interpolated paths; no uniform status/latency event | High | 1 |
| A process can over-consume or wait indefinitely for PostgreSQL | Engine relied on library pool defaults and had no command timeout | High | 1 |
| Failed dependency session cleanup was implicit | Request dependency did not explicitly roll back before re-raising | Medium | 1 |
| OAuth limiter does not scale across replicas | Fixed-window counters live in process memory | High | 2 |
| Forwarded client address trust is not explicit | Abuse controls can consume proxy-provided address without a documented trusted-proxy boundary | High | 2 |
| Endpoint policy is dispersed | Authentication, role, content-type and request-size expectations are not one enforceable manifest | High | 2 |
| Collection and expensive-operation budgets need proof | Pagination, query plans, N+1 behavior and worker concurrency lack measured budgets | High | 3 |
| Service signals do not yet expose saturation | SLOs exist, but RED/pool/job-age metrics and alert implementation are incomplete | High | 4 |

No critical secret exposure, cross-tenant bypass, or destructive data issue was
observed in this baseline. That is not a guarantee of absence: Phase 2 explicitly
tests each trust boundary, and Phase 3 validates database behavior with real plans
and load rather than static inspection alone.
