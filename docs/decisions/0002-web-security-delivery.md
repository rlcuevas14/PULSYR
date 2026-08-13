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

Executable Jinja page scripts and event handlers were extracted into the content-hashed application asset. Both the private app and public site therefore enforce `script-src` without `unsafe-inline`; private executable code is restricted to `self`, while the public site additionally permits the configured Plausible origin. Dynamic project and brand colors still require inline style attributes, so `style-src 'unsafe-inline'` remains the single temporary exception. Removing it requires replacing server-rendered color values with a finite class/token contract without reducing tenant customization.

## Credential encryption evaluation

Database encryption for Sentry and webhook credentials is not introduced in this phase. Correct key separation and rotation need an operator-managed encryption key, versioned ciphertext, backup/recovery rules, and a migration/rollback procedure. Implementing reversible encryption with the application `SECRET_KEY` would provide misleading protection. The immediate controls are write-only rendering, explicit replacement/deletion, redacted logs, database access restriction, and documented future key-management work.
