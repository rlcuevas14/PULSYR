# Phase 2 API/auth boundary audit

Date: 2026-08-13

## Implemented controls

- An executable inventory classifies every FastAPI operation as public, browser
  session, owner/superadmin, API token, session-or-token, MCP or signed webhook.
- Password, OAuth, MCP and webhook counters are atomic PostgreSQL rows shared by all
  replicas. Keys are HMAC digests and contain no raw IP or email.
- Password failures have an identity budget and a broader client-IP budget, limiting
  credential stuffing without penalizing successful sign-ins behind a shared NAT.
- Forwarded visitor addresses are honored only from explicitly configured direct
  proxy CIDRs; spoofed `CF-Connecting-IP` from other peers is ignored.
- Mutation bodies are read once under route-specific ceilings. Explicitly wrong
  API/MCP/webhook JSON or multipart media types are rejected before expensive work;
  a missing type remains accepted for compatibility with established signed senders.
- Machine boundary errors use a consistent `{error: {code, message, request_id}}`
  envelope where this does not change established application endpoint behavior.

## Review evidence

Generate the current inventory with:

```text
python scripts/export_route_inventory.py artifacts/api/route-inventory.json
```

The Phase 2 regression suite asserts critical trust classifications, proxy spoofing
behavior, privacy keys, atomic counter behavior, body replay, size limits and media
types. Existing tenant-isolation, CSRF, token scope, webhook signature and MCP tests
remain the authoritative authorization behavior gate.

## Deferred to later phases

- Phase 3 measures hot queries, pagination, queue concurrency and load budgets.
- Phase 4 exports rate-limit, request and saturation metrics and operational alerts.
- Deployment must set `TRUSTED_PROXY_CIDRS` to the actual Caddy-to-app network after
  inspecting that network; guessing a production CIDR would weaken this control.
