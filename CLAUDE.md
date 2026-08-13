# CLAUDE.md: Pulsyr

> Agent-native backlog manager for solo-preneurs. Manage N projects from one database;
> Any MCP client (Claude Code, Codex CLI, Grok CLI, Cursor, ...) reads and writes your
> backlog over HTTP, keeping it current as it works.

## What it is

- **Backlog + dependency graph + Sentry incidents + development threads + PMO Management (documents/pendings/Gantt)**: all accessible via 26 MCP tools.
- **Multi-project**: one database, N projects. Each MCP token is project-scoped; the agent cannot write to the wrong project.
- **Self-hosted OSS**: Docker + Postgres, no external dependencies except optional AI keys.
- **Repo**: `rlcuevas14/PULSYR` (open source).

---

## Current state

All merged to `main`:
- **OSS multi-project refactor**: standalone docker-compose + `.env.example`, `projects` table + `project_id` FKs, English enums (v0011), 17 English MCP tools with project isolation failsafe, `/projects` UI, setup wizard, public README.
- **Multi-account**: accounts/users/grants model (`accounts`, `project_members`), `create_account` service + super-admin UI (`/admin/accounts`) + owner member matrix (`/account/members`), per-project `viewer`/`editor`, account isolation across MCP + REST + UI (`projects/access.py` chokepoint). MCP token scope ≤ minter's role.
- **Backlog redesign + Archive** (spec 2026-07-06, shipped `v2026.07.06-1`): open-only default, FTS search with relevance order, board view, quick-filter chips, close-from-row (lifecycle-aware modal), group-by, `/archive` (ISO-week groups, reason+commit from events, AI weekly summary), SQL ordering. Routes renamed to English slugs 2026-07-16 (`/priority`, `/threads`, `/incidents`, `/archive`) with 301s from the old Spanish paths.
- **i18n**: full UI in English (default) / Spanish / French: JSON catalogs + `t()`/`tn()`, language selector in navbar/login/setup (see Conventions).
- **Public site**: Astro SSG in `site/`, deployed separately to Cloudflare Workers at `pulsyr.dev`; English lives at `/`, Spanish at `/es/`, and Cloudflare selects the initial language by IP while an explicit EN/ES choice wins.
- **Ambient video**: the home hero and its workflow contrast band use local decorative MP4 backgrounds, never standalone players. Keep them muted, looped, `playsinline`, control-free, script-free, poster-backed and compatible with reduced motion; see `docs/decisions/0003-public-site-ambient-video.md`.

---

## Stack

FastAPI + SQLAlchemy async (asyncpg) + Alembic + **Jinja2 + HTMX 2 (CDN) + Tailwind (CDN)**: no Node build. Postgres + pgvector. Asyncio worker in-process (queue in DB, `FOR UPDATE SKIP LOCKED`, no Redis). **MCP is hand-rolled** (JSON-RPC 2.0), NOT the `mcp` SDK: for auth/DB/testability control.

---

## Architecture (`app/`)

| Module | Responsibility |
|--------|----------------|
| `main.py` | `create_app`, lifespan (starts worker), mounts routers + `/mcp` (`mount_mcp`) |
| `config.py` | `Settings` (env vars) |
| `database.py` | engine, `SessionFactory`, `Base`, `get_db` |
| `templates_config.py` | `Jinja2Templates` + globals (`t`/`tn`/`current_lang`/`LANGS`, lifecycle helpers) + `fecha` filter |
| `i18n/` | JSON catalogs (`locales/{en,es,fr}.json`), `t()`/`tn()` (fallback lang→en→key), `resolve_lang` (session, default `en`) |
| `auth/` | `User` (`account_id`, `account_role` owner/member, `is_superadmin`, `password_hash` **nullable**)/`ApiToken`, bcrypt + SHA-256 tokens, deps (cookie **or** Bearer; `require_owner`/`require_superadmin`/`current_project_id`), `/setup` wizard. Routes live at the **root** (`/login`, `/logout`, `/password`, `/signup`) with 301/308 shims from the old `/auth/*`. `oauth.py` = GitHub/Google sign-in on httpx (no OAuth lib): only a provider-**verified** email is accepted, state is signed (the session cookie is SameSite=Strict and absent on the provider's cross-site redirect back), and with no `OAUTH_*` keys the buttons never render |
| `accounts/` | `Account` model (tenant), `service.py` (`create_account`: reusable for a future public signup), `members.py` (collaborator + grant matrix), router (`/admin/accounts` super-admin, `/account/members` owner) |
| `projects/` | `Project` model (`account_id`-scoped, slug unique per account), service, **`access.py`** (isolation chokepoint: `accessible_project_ids`, `user_role_on_project`, `require_project_access`, `resolve_project_id`/`resolve_current_project`), router (`/projects/*`, `/ui/project/switch`) |
| `items/` | `Item`/`ItemComment`/`ItemEvent`/`AiEnrichment`/`ItemRelationship`; `service.py` (lifecycle-validated mutations); `lifecycle.py` (8-state machine); `graph.py` (neighborhood/blocking/Kahn); `relationships.py` (arcs); `importer.py` (JSONL) |
| `scopes/` | `Scope` (area grouper) + router |
| `threads/` | `Thread`/`ThreadArtifact` (stages), service, router |
| `management/` | PMO tab (per-project): `Compartment`/`Deliverable`/`DeliverableVersion` (append-only, bytes in `bytea`), `Pending` (owner+status), `PlanTask` (Gantt 3-level hierarchy), `ManagementEvent` (audit). `gantt.py` = pure geometry (dynamic weeks→months axis, rollups); `service.py` (validated mutations + events); `router.py` (`/management/{documentos,plan,pendientes}`, upload/download). UI is a viewer; **Gantt edited only via MCP** |
| `webhooks/` | `SentryIssue`/`SentryConnection`; `connection.py` (account-level Sentry connection: webhook token, config, slug routing, re-attach: tenancy chokepoint for webhooks); service (HMAC verify, dual-shape payload parse, ingest w/ account+project stamping, backfill, fetch stack trace, resolve w/ 429 retry); router (tokened `POST /webhooks/sentry/{token}` + deprecated legacy global route) |
| `jobs/` | `AgentRun`; `worker.py` (poll-and-lease); `handlers.py` (`enrich`, `triage-sentry`) |
| `ai/` | `llm.py`: isolated/mockable interface to Haiku (enrich/triage/generate_stage) + Gemini (embed). Degrades without API key |
| `mcp/` | `server.py` (JSON-RPC transport + 26 tool registry + auth/scope + project-id failsafe); `tools.py` (implementations) |
| `ui/` | `router.py`: screens (`/` card-launcher home, `/backlog`, `/priority`, `/threads`, `/incidents`, `/archive`, `/items/{id}`, `/admin`) + `/ui/...` HTMX action endpoints; `flash.py` (`flash_success`: pop-once session flash → celebration overlay on completions / green toast) |

---

## Data model (real enum values: do NOT invent)

**`items`**: `id, project_id, scope_id, title, summary_md, type, status, priority, effort_ai, impact_ai, impact_rationale, effort_declared, priority_declared, trigger_text, dependencies, origen, source_refs(JSONB), stale_risk, agent_ready, created_by, created_at, updated_at, closed_at, last_touched_at, thread_id` + `embedding vector(768)` (migration-only) + `search_vector` (GENERATED, migration-only).

- **status**: `idea, backlog, spec, in-progress, blocked, in-review, done, discarded`
- **type**: `bug, feature, tech-debt, infra, docs, ops, security, product, idea`
- **priority**: `p0..p3` · **effort_ai**: `XS..XL` · **impact_ai**: `1..5`
- **origen**: `digest, human, ai-session, sentry, agent`
- **item_comments.kind**: `comment, ai-analysis, decision, status-change` (`decision` = decision log)

**Multi-account (tenancy)**: **`accounts`** (`id, name, slug, is_active`) groups projects. **`users`** gain `account_id` (FK: one account per user), `account_role` (`owner`|`member`), `is_superadmin` (instance operator). **`projects`** gain `account_id`; slug is unique **per account**. **`project_members`** (`user_id, project_id, role` ∈ `viewer`|`editor`) is the per-project grant matrix: owners have implicit editor on every project of their account. **`scopes.name`** is unique **per project** (`(project_id, name)`), not globally. MCP token scope ≤ minter's role on the project.

**Other tables**: `accounts, users, api_tokens, projects, project_members, scopes, item_comments, item_events, ai_enrichments, sentry_issues, agent_runs, item_relationships, threads, thread_artifacts`.
`item_events(actor, action, payload)` is the **audit primitive**: every mutation must emit one.

**Migrations** (head = `v0021`): v0001 (9 tables) · v0002 (search_vector+GIN) · v0003 (item_relationships) · v0004 (last_touched_at + source_refs→JSONB) · v0005 (threads + items.thread_id) · v0006–v0010 (projects + project_id FKs) · v0011 (English enum rename) · v0012 (accounts + project_members + account columns; backfills existing data into one default account, earliest/admin user → owner+superadmin; scopes.name → unique per project) · v0013 (project isolation hardening) · v0014 (accounts layer) · v0015 (relax project_id back to nullable: isolation is code-enforced) · v0016 (management/PMO domain: compartments, deliverables + append-only deliverable_versions, pendings, plan_tasks, management_events) · v0017 (sentry_connections account-level + projects.sentry_project_slug unique-per-account + sentry_issues.account_id; drops the 3 never-read per-project sentry columns) · v0018 (English thread stages + artifact kinds) · v0019 (English `agent_runs.status` `pending`/`running` + `sentry_issues.triage` `pending`/`real-bug`/`bad-input`/`noise`: the last Spanish values that reached the API surface) · v0020 (`users.password_hash` NULLABLE: OAuth users have no password here) · v0021 (hosted account subscriptions, versioned consent and durable OAuth identities).

**Thread stages** (English since v0018): `idea, research, stories, spec, in-development, review, done, discarded` · artifact kinds: `research, stories, spec, notes, decision`

---

## MCP: 26 tools

Connect any MCP client to a project (generate token at `/projects/{slug}/settings`). All a
client needs is the URL `<base>/mcp` plus `Authorization: Bearer <TOKEN>`; the Claude Code
shortcut for that is:
```bash
claude mcp add --transport http my-project http://localhost:8000/mcp \
  --header "Authorization: Bearer <TOKEN>"
```
`protocolVersion 2025-03-26`. Bearer auth required; write tools require scope `write` (else → `isError`). **New tools only appear after RESTARTING the client** (in Claude Code, don't-ask denies unapproved tools; client-side, not a server bug).

Token MUST have `project_id` set (created from `/projects/{slug}/settings`, not `/admin`). MCP dispatch returns `isError` immediately if `token.project_id is None`.

- **Read**: `pulsyr_context`, `pulsyr_search`, `pulsyr_list`, `pulsyr_areas`, `pulsyr_incidents`, `pulsyr_incident`, `pulsyr_thread_list`, `pulsyr_thread`
- **Write**: `pulsyr_create` (accepts `thread_id`), `pulsyr_advance`, `pulsyr_complete`, `pulsyr_link`, `pulsyr_move_area`, `pulsyr_incident_resolve`, `pulsyr_thread_create`, `pulsyr_thread_advance`, `pulsyr_thread_link`
- **Management (PMO)**: documentos: `pulsyr_doc_list`, `pulsyr_doc_get`, `pulsyr_doc_put` (r/w) · pendientes: `pulsyr_pending_list`, `pulsyr_pending_upsert`, `pulsyr_pending_complete` · gantt: `pulsyr_gantt_get`, `pulsyr_gantt_task_upsert`, `pulsyr_gantt_task_remove`. The Gantt is **edited only via MCP** (UI renders it read-only).
- **Prompts**: `briefing`, `decision`. **Resources**: `pulsyr://area/{name}`, `pulsyr://graph/{item_id}`.

Items returned include `area` (name) and `thread_id` when set. Graph is item↔item; thread membership is via `thread_id`, not the graph.

---

## Key concepts

- **Item lifecycle** (`lifecycle.py`): 8-state machine with transition matrix, validated in UI/REST/MCP. Terminals (`done`/`discarded`) go through `/close` (require a reason).
- **Live graph**: blocking is **derived** (an item is blocked if it has an open `blocks` arc incoming), not a stored state. `pulsyr_context` traverses neighborhood in real time (anti context-collapse).
- **Incidents (Sentry)**: errors land in `sentry_issues` (**container**, NOT auto-promoted to backlog). AI triage pre-classifies noise; owner/agent **manually promotes** real ones. `pulsyr:UUID` in commit auto-closes (GitHub webhook). **Per-project routing (spec 2026-07-10)**: 1 account ↔ 1 Sentry org: owner configures the connection once at `/account/integrations` (tokened webhook URL + optional HMAC client secret + optional API token/org/base_url for resolve+backfill); each project sets its `sentry_project_slug` in its settings; inbound `POST /webhooks/sentry/{token}` routes by slug **within the token's account** (cross-account collisions can't leak). Unmatched events park with `project_id=NULL` + account stamp; "Re-attach unmatched" fixes them after slug changes. Global `SENTRY_*` env vars are legacy fallback only.
- **Threads**: funnel for heavy features (80% goes fast through backlog, no thread needed).
- **Append-only / audit**: every mutation emits `ItemEvent`.
- **Account & project isolation**: an **account** groups projects; a user belongs to one account as `owner` or `member`, with per-project `viewer`/`editor` grants (`project_members`; owners get implicit editor on all account projects). The chokepoint `projects/access.py` resolves the effective project for every REST/UI request (`resolve_project_id`/`resolve_current_project`) and `mcp/tools.py` filters by `token.project_id`. Cross-account access is impossible: members see only granted projects; the super-admin (instance operator) manages accounts but not their backlog data. `create_user` with no `account_id` auto-creates a personal account + `Default` project (tests/simple flows).

---

## Run locally + tests

**pgvector not required locally** (degrades gracefully: `embedding` is migration-only column). Point `TEST_DATABASE_URL` at any local Postgres with an empty database, and set `DEBUG=true` (without it the session cookie gets the `secure` flag and every UI test 303-redirects to login):

```bash
TEST_DATABASE_URL="postgresql+asyncpg://<user>:<password>@localhost:5432/pulsyr_test" \
  DEBUG=true SECRET_KEY=any-test-secret \
  python -m pytest tests/ -q
ruff check app/ tests/
python -m mypy app/
```

**Dirty DB gotcha**: `pulsyr_test` persists between runs; `create_all` does NOT alter existing tables. If you change the schema or see failures that don't happen in CI, **reset**: `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` then re-run.
**`search_vector`** (GENERATED, migration-only) is globally patched in `conftest.py` so full-text works in all tests.
**CI is the real gate** (pgvector/pgvector:pg16). Push to `main` runs CI; deploy is NOT automatic.

---

## Deploy

### Private application

```bash
git tag -a v2026.MM.DD-N -m "..." && git push origin <tag>
```
Triggers `deploy.yml`: multi-platform build (amd64+arm64) → push to GHCR → SSH to server → `docker compose pull && up -d` → `alembic upgrade head`.

**Required secrets** (`.env` on server): `DB_PASSWORD`, `SECRET_KEY`.
**Optional**: `ANTHROPIC_API_KEY` (AI triage/enrich), `GEMINI_API_KEY` (embeddings), `SENTRY_CLIENT_SECRET`, `SENTRY_API_TOKEN`, `SENTRY_ORG`, `GITHUB_WEBHOOK_SECRET`.

### Public site

The public site is a separate Cloudflare Worker deployment and does not use the private
application tag workflow:

```bash
cd site
npm ci
npm run check
npm run build
npm run check:budget
npm run check:html
npm run test:e2e
npm run deploy:dry-run
npm run deploy
```

Deploy only after the `main` CI run is green. `npm run deploy` uses
`wrangler deploy --keep-vars`. After deployment, verify `/`, `/es/`, the local media
responses and the Spanish hero at 320 px. Public pages must remain free of unexpected
executable JavaScript; ambient videos therefore use native autoplay rather than a
playback controller script.

---

## Conventions

- **English at the API level**: all enum values (thread stages included since v0018), API/MCP error messages, and MCP tool names are English. Display labels are translated via the i18n catalogs (`status.*`, `stage.*`, …).
- **i18n (UI copy)**: NEVER hardcode user-visible strings in templates or UI routers. Templates use the request-scoped Jinja globals `t("domain.key")` / `tn("domain.key", n)`; Python uses `app.i18n.t(key, resolve_lang(request))`. Catalogs: `app/i18n/locales/{en,es,fr}.json` (flat dot-namespaced keys; English is source of truth and fallback). Enum display labels: `t("status." ~ x)` / `type.*` / `origin.*` / `stage.*`. Language selector lives in the navbar (session-based, `GET /ui/lang/{code}`); default English. `tests/test_i18n.py` enforces catalog completeness, placeholder parity, and template coverage: adding a key to one catalog without the other two fails CI. Careful: a Jinja loop variable named `t` shadows the translation global: never use it.
- **Every feature brings tests**; CI green before tagging.
- **Public visual media**: background videos are decorative composition layers, not product demos. Do not add controls or standalone video cards; use them sparingly, keep text/CTA above a tested contrast overlay, and update the explicit media budgets when replacing assets.
- **UI design system**: all tokens + `.p-*` component classes live in `app/templates/partials/_head.html` (Tailwind CDN + CSS variables; `darkMode:'class'`, light=Clay-cream / dark=warm near-black, per-project `--accent` from session). Never hardcode gray/blue palette classes in templates; never use opacity modifiers on semantic tokens (`bg-canvas/50` silently breaks: allowed only on `brand-*`/`success`/`warning`/`error`). Success feedback via `flash_success` (`app/ui/flash.py`); forms hitting handlers that return `204 + HX-Refresh` MUST be `hx-post` (plain forms dead-end on 204).
- **LLM always via `app/ai/llm.py`** (isolated and mockable); degrades without API key, never breaks the worker.
- **Trunk-based**: direct commit to `main` allowed; verify locally (ruff+mypy+pytest for the area) before pushing; deploy only by tag.
- External webhooks/writes: verify HMAC signature, emit `ItemEvent`, sanitize untrusted content (XSS).
- `/admin` token creation does NOT set `project_id`: only use `/projects/{slug}/settings` to generate MCP tokens.

---

## How to resume

1. Read this CLAUDE.md.
2. First run: open `http://localhost:8000` → redirects to `/setup` (create account + first project + write token).
3. Local tests against `pulsyr_test` (reset schema if you suspect dirty DB).
4. Changes → CI green → tag for deploy.
