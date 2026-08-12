# Staging, promotion and rollback runbook

This procedure is intentionally versioned without provisioning infrastructure. The
P5 implementation did not create DNS, change a proxy, publish an image or deploy.

## Isolation contract

`staging.pulsyr.dev` and `app-staging.pulsyr.dev` require separate database storage,
`SECRET_KEY`, OAuth applications, webhook secrets and Sentry project. Both hosts are
protected by proxy authentication and `X-Robots-Tag: noindex, nofollow`; application
responses also carry the private-origin noindex policy. Public analytics must have
`PUBLIC_DEPLOYMENT_ENVIRONMENT=staging`, which prevents the snippet from rendering.

Copy `.env.staging.example` to `.env.staging` in the operator's secret-managed host.
Never commit it. Merge `infra/Caddyfile.staging.example` into the operator-owned proxy
only after replacing the password hash through its secret store.

## Candidate deployment by digest

1. Build the candidate image once in the release pipeline and record its registry
   digest, commit SHA and SBOM. A tag is a label, never the promotion identity.
2. Validate the reference:
   `python scripts/verify_release_image.py ghcr.io/owner/pulsyr@sha256:<digest>`.
3. Set `PULSYR_IMAGE` and `RELEASE` to that immutable candidate in the staging secret
   file. Validate with `docker compose --env-file .env.staging -f compose.staging.yml config`.
4. Back up the staging database, then run migrations using the candidate image:
   `docker compose --env-file .env.staging -f compose.staging.yml run --rm app-staging alembic upgrade head`.
5. Start staging and require `/health/live` and `/health/ready` to return 200 behind
   the authenticated proxy. Run auth, setup, MCP, webhook, 404/500 and browser smoke
   checks. Trigger a synthetic captured exception in the staging Sentry project.
6. Record approval, image digest, migration revision and smoke evidence. Promotion
   changes only the production digest to this exact value; it never rebuilds.

## Application rollback

Keep the previous known-good digest and its database compatibility notes. To roll
back, restore that digest, run `python scripts/verify_release_image.py`, and restart
only the application service. Verify liveness, readiness, login and one read-only MCP
request. Retain the failed release's request IDs and Sentry events for investigation.

## Migration rollback

Application rollback and schema downgrade are separate decisions. Before a release,
classify its migration as backward-compatible or restore-required. Prefer additive,
expand/contract migrations so the previous application can run on the new schema.
Only run `alembic downgrade <approved_revision>` when the migration's downgrade was
validated against a disposable backup. For destructive/incompatible changes, stop
writes and restore the pre-migration database backup instead of improvising a
downgrade.

## Completion evidence still requiring operator action

Creating DNS, proxy secrets, uptime monitors and a real promotion/rollback exercise
requires deployment authority and is deliberately outside this no-deploy phase. The
operator must attach timestamps, digest, backup identifier, smoke results and alert
delivery evidence to the release record before declaring external staging proven.
