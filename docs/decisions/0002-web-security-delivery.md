# ADR 0002: Private web security and asset delivery

**Status:** accepted · **Date:** 2026-08-12

## Decision

- Compile Tailwind during development/CI and commit content-hashed CSS/JavaScript used by the Python image.
- Vendor HTMX globally and load Sortable only on the backlog route. Production HTML has no runtime dependency on third-party CDNs or Google Fonts.
- Apply a double-submit CSRF token to every cookie-authenticated mutation. Webhooks, MCP, and Bearer-authenticated calls remain outside that browser-session boundary.
- Enforce CSP, framing, MIME, referrer and permissions headers in the app; add HSTS in non-debug mode without `includeSubDomains` or preload until every Pulsyr subdomain is externally validated.
- Keep authenticated HTML and APIs `private, no-store`; only content-hashed assets receive one-year immutable caching.
- Preserve stored integration secrets when a submitted secret field is blank. Replacement is write-only and deletion requires an explicit checkbox.
- Enable gzip in the app and document zstd/gzip at Caddy. Proxy compression remains the preferred production layer.

## Temporary CSP exception

The existing Jinja UI still contains inline style attributes, page scripts, and event handlers. CSP is enforced with `unsafe-inline` for script/style during their staged extraction. Network code remains restricted to `self`, framing is denied, objects are disabled, and all CDN dependencies are removed. Removing this exception is tracked with the UI extraction work; enabling a stricter directive before that refactor would disable existing controls.

## Credential encryption evaluation

Database encryption for Sentry and webhook credentials is not introduced in this phase. Correct key separation and rotation need an operator-managed encryption key, versioned ciphertext, backup/recovery rules, and a migration/rollback procedure. Implementing reversible encryption with the application `SECRET_KEY` would provide misleading protection. The immediate controls are write-only rendering, explicit replacement/deletion, redacted logs, database access restriction, and documented future key-management work.
