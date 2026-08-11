# Contributing to Pulsyr

Thanks for your interest! Pulsyr is a small, focused codebase: most
contributions land fast if they follow the conventions below.

## Development setup

Requirements: Python 3.12+ and a local Postgres (pgvector NOT required for
development: the embedding column is migration-only and degrades gracefully).

```bash
git clone https://github.com/rlcuevas14/PULSYR
cd PULSYR
pip install -e ".[dev]"
```

## Running tests

Point `TEST_DATABASE_URL` at any empty local Postgres database. `DEBUG=true`
is required (without it the session cookie is marked `secure` and every UI
test 303-redirects to login):

```bash
TEST_DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/pulsyr_test" \
  DEBUG=true SECRET_KEY=any-test-secret \
  python -m pytest tests/ -q
```

**Dirty DB gotcha:** the test database persists between runs and tables are
not altered in place. If you change the schema and see failures that don't
happen in CI, reset it: `DROP SCHEMA public CASCADE; CREATE SCHEMA public;`

## Quality gates (all must pass before a PR)

```bash
ruff check app/ tests/
python -m mypy app/
python -m pytest tests/ -q
```

CI runs the same gates against `pgvector/pgvector:pg16` and enforces **90%
test coverage**. Every feature or bugfix brings its own tests.

## Conventions

- **English everywhere**: code, comments, docstrings, enum values, API/MCP
  error messages, commit messages, and docs.
- **i18n**: never hardcode user-visible strings in templates or UI routers.
  Use the `t("domain.key")` / `tn()` Jinja globals and add the key to **all
  three** catalogs (`app/i18n/locales/{en,es,fr}.json`): `tests/test_i18n.py`
  fails CI otherwise. English is the source of truth.
- **Audit trail**: every item mutation must emit an `ItemEvent`.
- **UI design system**: all tokens and `.p-*` component classes live in
  `app/templates/partials/_head.html`. Never hardcode gray/blue palette
  classes; never use opacity modifiers on semantic tokens (`bg-canvas/50`
  silently breaks: allowed only on `brand-*`/`success`/`warning`/`error`).
  Forms hitting handlers that return `204 + HX-Refresh` must use `hx-post`.
- **LLM calls** always go through `app/ai/llm.py` (isolated and mockable;
  must degrade without an API key).
- **External webhooks/writes**: verify HMAC signatures, emit events, sanitize
  untrusted content.
- **Trunk-based**: PRs target `main`. Deploys happen only via `v*` tags.

## Reporting security issues

See [SECURITY.md](SECURITY.md): please don't open public issues for
vulnerabilities.
