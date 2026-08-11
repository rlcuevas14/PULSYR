# Connecting your agent to Pulsyr (MCP over HTTP)

Pulsyr exposes an MCP endpoint at `https://<your-pulsyr-host>/mcp` (Streamable HTTP, JSON mode).
Nothing to install locally: any MCP client that speaks HTTP connects with just a token.
That includes Claude Code, Codex CLI, Grok CLI, Cursor, Windsurf and Zed, among others.

Everything a client needs is two values:

| | |
|---|---|
| **URL** | `https://<your-pulsyr-host>/mcp` |
| **Header** | `Authorization: Bearer <YOUR_TOKEN>` |

The rest of this page is where to put them.

## 1. Generate a token

Tokens are **project-scoped**: go to `https://<your-pulsyr-host>/projects/<slug>/settings` →
**Generate MCP token** (scope `write` to create/close items from a session). Copy the token -
it is shown only once.

> Do NOT use `/admin` to mint MCP tokens: tokens created there have no `project_id` and the
> MCP endpoint rejects them.

## 2. Register the server in your agent

**A `.mcp.json` in the repo root** is understood by most clients and is the one form
worth learning:
```json
{
  "mcpServers": {
    "my-project": {
      "type": "http",
      "url": "https://<your-pulsyr-host>/mcp",
      "headers": { "Authorization": "Bearer ${PULSYR_TOKEN}" }
    }
  }
}
```
Clients that support it expand `${PULSYR_TOKEN}` from the environment, so the token
stays out of git. Never commit the literal token.

**Claude Code** also has a one-liner that writes the same thing to `~/.claude.json`:
```bash
claude mcp add --transport http my-project https://<your-pulsyr-host>/mcp \
  --header "Authorization: Bearer <YOUR_TOKEN>"
```
Verify with `claude mcp list`.

**Other clients** (Codex CLI, Grok CLI, Cursor, Windsurf, Zed) each keep their MCP
servers in their own config file, with their own key names. Check your client's MCP
documentation for where its server list lives, then give it the URL and the
Authorization header from the table above. Pulsyr does not care which one asks.

Whatever the client, newly added tools usually appear only after **restarting** it.

## 3. Available tools (26)

| Tool | Scope | Purpose |
|------|-------|---------|
| `pulsyr_context(area?, work_description?)` | read | Session briefing: quick wins, blockers, unlinked incidents, active threads |
| `pulsyr_search(q, area?, type?, limit?)` | read | Full-text search |
| `pulsyr_list(area?, status?, type?, order?, quickwins?, limit?)` | read | Filtered list (order: `impact`/`priority`/`topological`/`recent`) |
| `pulsyr_areas()` | read | List areas (backlog groupings) with counts and examples |
| `pulsyr_incidents(status?, triage?, limit?)` | read | List Sentry incidents |
| `pulsyr_incident(issue_id)` | read | Incident detail (with stack trace when available) |
| `pulsyr_thread_list(stage?)` | read | List development threads |
| `pulsyr_thread(thread_id)` | read | Thread detail with artifacts and linked items |
| `pulsyr_create(title, type, area_name, …)` | write | Create item (origin `ai-session`; creates the area if missing) |
| `pulsyr_advance(item_id\|query, to_status)` | write | Change status (lifecycle-validated; terminals go via `pulsyr_complete`) |
| `pulsyr_complete(item_id\|search_query, note?, commit_sha?)` | write | Mark done + report newly unblocked items |
| `pulsyr_link(source, target, relation, note?)` | write | Create a graph edge (`blocks`/`requires`/`conflicts`/`related`/`part_of`) |
| `pulsyr_move_area(item_id\|query, area_name)` | write | Move an item to another existing area |
| `pulsyr_incident_resolve(issue_id, note?)` | write | Resolve a Sentry incident |
| `pulsyr_thread_create(title, area_name, summary?)` | write | Create a development thread |
| `pulsyr_thread_advance(thread_id, artifact_content?)` | write | Advance a thread to its next stage |
| `pulsyr_thread_link(thread_id, item_id\|query)` | write | Link an item to a thread |
| `pulsyr_doc_list(compartment_id?, status?, q?)` | read | List Management deliverables (metadata only) |
| `pulsyr_doc_get(deliverable_id, include_content?)` | read | Deliverable detail + version history (inlines content up to 256 KB) |
| `pulsyr_doc_put(compartment, name, doc_type, content\|content_base64, …)` | write | Create a deliverable or append a version (append-only; auto-creates the compartment) |
| `pulsyr_pending_list(status?, owner?, overdue?, plan_task_id?)` | read | List project pendings (action items) |
| `pulsyr_pending_upsert(pending_id?, title?, status?, due_date?, owner?, …)` | write | Create or update a pending (omit `pending_id` to create) |
| `pulsyr_pending_complete(pending_id)` | write | Mark a pending as done |
| `pulsyr_gantt_get()` | read | Full project plan: task hierarchy, dates, progress, milestones, deps |
| `pulsyr_gantt_task_upsert(task_id?, name?, parent_id?, start_date?, end_date?, progress?, …)` | write | Create or update a Gantt task (max 3 levels; the Gantt is edited only via MCP) |
| `pulsyr_gantt_task_remove(task_id)` | write | Delete a Gantt task (children cascade) |

Prompts: `briefing`, `decision`. Resource templates: `pulsyr://area/{name}`, `pulsyr://graph/{item_id}`.

## 4. Breaking change: tool rename (Spanish → English)

Older Pulsyr versions exposed Spanish tool names. They were renamed once, before the first
public release:

| Old (removed) | Current |
|---------------|---------|
| `pulsyr_contexto` | `pulsyr_context` |
| `pulsyr_buscar` | `pulsyr_search` |
| `pulsyr_listar` | `pulsyr_list` |
| `pulsyr_crear` | `pulsyr_create` |
| `pulsyr_avanzar` | `pulsyr_advance` |
| `pulsyr_completar` | `pulsyr_complete` |
| `pulsyr_relacionar` | `pulsyr_link` |

Enum values were also renamed (statuses, types, origins: e.g. `hecho` → `done`,
`ia-sesion` → `ai-session`). If an old client sends Spanish values, calls fail validation -
update the client; there is no compatibility shim.

v0018 completed the rename for threads (same no-shim policy):

| Old (removed) | Current |
|---------------|---------|
| stage `investigacion` | `research` |
| stage `historias` | `stories` |
| stage `en-desarrollo` | `in-development` |
| stage `hecho` | `done` |
| stage `descartado` | `discarded` |
| artifact kind `investigacion` | `research` |
| artifact kind `historias` | `stories` |
| artifact kind `notas` | `notes` |

## 5. Suggested session protocol

- **Session start**: call `pulsyr_context` to get current priorities, blockers, and open incidents.
- **During work**: `pulsyr_create` for anything worth tracking; `pulsyr_advance` as states change.
- **Session end**: `pulsyr_complete` with `note` + `commit_sha` for everything shipped: the
  commit links the item to code, and the note becomes the close reason shown in the Archive.
