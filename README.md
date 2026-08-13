# Pulsyr

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Agent-native backlog manager for solo-preneurs.** Manage multiple projects from one self-hosted instance. Your coding agent connects over MCP and keeps the backlog up to date as it works, with no manual updates.

---

## How it works

You run Pulsyr on your own server (or locally). Your agent connects to it as an MCP server over HTTP. As it codes, it reads context, creates items, advances statuses and closes completed work on its own.

Pulsyr is an MCP server, not a Claude Code plugin: it is built and used daily with Claude Code, and works the same with Codex CLI, Grok CLI, Cursor, Windsurf, Zed, or anything else that speaks MCP over HTTP.

Each project gets its own MCP token. The agent cannot write to the wrong project.

---

## Quick start

**Prerequisites:** Docker, Docker Compose, a Postgres instance (included in the compose file).

```bash
git clone https://github.com/rlcuevas14/PULSYR
cd PULSYR
cp .env.example .env
```

Edit `.env`: at minimum set `SECRET_KEY` and `DB_PASSWORD`:

```bash
SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
DB_PASSWORD=choose-a-password
```

```bash
docker compose up -d
```

The compose file pulls the prebuilt public image
[`ghcr.io/rlcuevas14/pulsyr`](https://github.com/rlcuevas14/PULSYR/pkgs/container/pulsyr) -
no local build needed.

Open **http://localhost:8000** → redirects to `/setup` to create your account, first project, and write token in one step.

### Connect your agent

After setup, go to **Projects → Settings**. It shows the two values any MCP client needs:

```
URL:    http://localhost:8000/mcp
Header: Authorization: Bearer <TOKEN>
```

Claude Code has a one-liner for it, also shown on that page:

```bash
claude mcp add --transport http my-project http://localhost:8000/mcp \
  --header "Authorization: Bearer <TOKEN>"
```

For other clients, put the same URL and header wherever they keep MCP servers (see
[docs/MCP.md](docs/MCP.md)). Restart the client, then call `pulsyr_context` at the start
of a session to get your current priorities, blockers and open incidents.

---

## MCP tools (26)

| Tool | What it does |
|------|--------------|
| `pulsyr_context` | Session briefing: quick wins, blockers, unlinked Sentry errors, active threads |
| `pulsyr_search` | Full-text search across backlog items |
| `pulsyr_list` | Filtered item list (status, type, area, order) |
| `pulsyr_areas` | List areas (groupings) with item counts |
| `pulsyr_create` | Create a backlog item; auto-creates the area if needed |
| `pulsyr_advance` | Transition an item's status (lifecycle-validated) |
| `pulsyr_complete` | Mark an item done: reports newly unblocked items |
| `pulsyr_link` | Add a graph edge between items (`blocks` / `requires` / `conflicts` / `related` / `part_of`) |
| `pulsyr_move_area` | Move an item to a different area |
| `pulsyr_thread_create` | Create a Thread (funnel for heavy features) |
| `pulsyr_thread_advance` | Advance a Thread to its next stage |
| `pulsyr_thread_list` | List Threads, filter by stage or area |
| `pulsyr_thread` | Thread detail with artifacts and linked items |
| `pulsyr_thread_link` | Link an existing backlog item to a Thread |
| `pulsyr_incidents` | List Sentry errors in the incident container |
| `pulsyr_incident` | Incident detail with stack trace fetched from Sentry |
| `pulsyr_incident_resolve` | Resolve an incident in Pulsyr (and optionally in Sentry) |
| `pulsyr_doc_list` | List management documents (deliverables) grouped by compartment |
| `pulsyr_doc_get` | Read a management document (current or a specific version) |
| `pulsyr_doc_put` | Create or update a management document (append-only versioning) |
| `pulsyr_pending_list` | List project pendings (action items) with owner and status |
| `pulsyr_pending_upsert` | Create or update a pending |
| `pulsyr_pending_complete` | Mark a pending as done |
| `pulsyr_gantt_get` | Read the project plan (3-level Gantt hierarchy) |
| `pulsyr_gantt_task_upsert` | Create or update a plan task (the Gantt is edited only via MCP) |
| `pulsyr_gantt_task_remove` | Remove a plan task |

---

## Item lifecycle

```
idea → backlog → spec → in-progress → in-review → done
                              ↕
                           blocked
                              ↓
                          discarded  (available from any state)
```

Transitions are validated: the agent can't make illegal moves. Terminal states (`done` / `discarded`) require a reason. Blocking is **derived**: an item is blocked when it has an open `blocks` arc incoming; no manual flag needed.

---

## Features

- **Dependency graph**: typed edges between items. Blocked status computed in real time, never stale.
- **Priority matrix**: impact × effort, AI-estimated via Claude Haiku. Quick wins surface automatically on the dashboard.
- **Multi-project**: N projects, one database. Token-level isolation: each MCP token is bound to exactly one project.
- **Threads**: a lightweight funnel for features too big to go straight to the backlog (idea → investigation → stories → spec → in-development → review → done).
- **Sentry integration**: errors land in a dedicated incident container. AI triage pre-classifies noise. You (or the agent) promote real issues to the backlog manually.
- **GitHub webhook**: include `pulsyr:ITEM-UUID` in any commit message to auto-close the referenced item.
- **AI enrichment**: impact/effort estimation via Claude Haiku. Optional; degrades gracefully without `ANTHROPIC_API_KEY`.
- **Semantic search**: embedding-based neighbor lookup via Gemini + pgvector. Optional; requires both `GEMINI_API_KEY` and a Postgres instance with pgvector.
- **No Node.js**: Tailwind and HTMX load from CDN. Server renders HTML; HTMX handles partial updates.
- **Sign-in**: email and password out of the box. Optionally "Continue with GitHub/Google", which also removes the need for this app to ever send email: the provider vouches for the address, so there is no confirmation mail and no password reset to build. With no OAuth keys set, the buttons never render.
- **Multilingual UI**: English and Spanish, selected from the edge country/browser preference and switchable from the navbar. Adding a language = one JSON file in `app/i18n/locales/` (CI enforces catalog completeness).
- **Archive**: closed items grouped by ISO week with close reasons and linked commits, plus an on-demand AI weekly summary.

---

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | Session signing key. Generate: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DB_PASSWORD` | Yes | Postgres password |
| `BASE_URL` | No | Public URL of this instance. Required for OAuth: the callback is derived from it |
| `OAUTH_GITHUB_CLIENT_ID` / `_SECRET` | No | Enables "Continue with GitHub". Callback: `BASE_URL` + `/callback/github` |
| `OAUTH_GOOGLE_CLIENT_ID` / `_SECRET` | No | Enables "Continue with Google". Callback: `BASE_URL` + `/callback/google` |
| `PUBLIC_SIGNUP` | No | `true` lets anyone with a provider account register. Default `false` |
| `FREE_MAX_PROJECTS` | No | Active projects included in hosted Free accounts (default `1`) |
| `FREE_MAX_MEMBERS` | No | Additional teammates included in hosted Free accounts (default `1`) |
| `FREE_MAX_TOKENS_PER_PROJECT` | No | Active MCP tokens per project on Free (default `3`) |
| `FREE_MAX_STORAGE_MB` | No | Total document-version storage on Free (default `25`) |
| `OAUTH_RATE_LIMIT_ATTEMPTS` | No | OAuth attempts allowed per client/window (default `20`) |
| `OAUTH_RATE_LIMIT_WINDOW_SECONDS` | No | OAuth fixed-window duration (default `600`) |
| `TERMS_VERSION` | No | Legal notice version recorded with hosted signup consent |
| `ANTHROPIC_API_KEY` | No | Enables AI impact/effort estimation and Sentry triage |
| `GEMINI_API_KEY` | No | Enables semantic neighbor search (requires pgvector) |
| `SENTRY_CLIENT_SECRET` | No | HMAC secret for Sentry webhook signature verification |
| `SENTRY_API_TOKEN` | No | Fetches stack traces and resolves issues via Sentry API |
| `SENTRY_ORG` | No | Your Sentry organization slug |
| `GITHUB_WEBHOOK_SECRET` | No | HMAC secret for GitHub webhook (auto-close on commit) |
| `DATABASE_URL` | No | Full async DSN; overrides the compose-built URL (needed when running without Docker Compose) |
| `DEBUG` | No | `true` relaxes cookie security for local HTTP development. Never enable in production |
| `PORT` | No | Host port published by docker-compose (default `8000`) |
| `IMAGE_TAG` | No | Image tag pulled by docker-compose (default `latest`) |

See `.env.example` for all defaults.

---

## Development

```bash
pip install -e ".[dev]"

# Run tests (Postgres required: any empty database works; pgvector NOT required.
# DEBUG=true is mandatory: without it the session cookie is `secure` and UI tests 303-redirect)
TEST_DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/pulsyr_test" \
  DEBUG=true SECRET_KEY=any-test-secret \
  python -m pytest tests/ -q

# Lint + type check
ruff check app/ tests/
python -m mypy app/
```

**Dirty DB gotcha:** the test database persists between runs. If you change the schema and see unexpected failures, reset it:
```sql
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
```
Then re-run pytest: it recreates everything from scratch.

---

## Deployment

Tag a release; the GitHub Actions workflow builds a multi-platform image (amd64 + arm64), pushes to GHCR, SSHs to your server, and runs `alembic upgrade head`:

```bash
git tag -a v2026.MM.DD-1 -m "what changed"
git push origin v2026.MM.DD-1
```

The build-and-push half works in any fork out of the box (GHCR auth uses the built-in `GITHUB_TOKEN`). The deploy half SSHs into your own server and needs the `VM_HOST`, `VM_USER`, and `VM_SSH_KEY` secrets in your repo settings. See `.github/workflows/deploy.yml` for the full spec.

---

## Stack

FastAPI · SQLAlchemy async (asyncpg) · Alembic · Jinja2 · HTMX 2 · Tailwind CSS (CDN) · Postgres + pgvector · MCP JSON-RPC 2.0 (hand-rolled, not the SDK)

---

## Contributing & security

Contributions welcome: see [CONTRIBUTING.md](CONTRIBUTING.md). To report a
security vulnerability privately, see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © 2026 Avonlea Systems SpA

The code is MIT. The **name and logo** are trademarks: fork freely, but ship it
under your own name. See [TRADEMARK.md](TRADEMARK.md).
