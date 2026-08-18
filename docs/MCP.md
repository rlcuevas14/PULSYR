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

## 3. Dynamic, module-aware discovery (45 tools)

Pulsyr keeps one `/mcp` URL and one project token. The server filters `tools/list`,
`prompts/list`, `resources/list`, and `resources/templates/list` from the project's
effective modules. A direct call to a disabled family returns `module_disabled`
before checking write scope or looking up an entity. Disabling a module hides its
surface but does not delete its data.

Start a session with `pulsyr_capabilities()`. It reports each module's configured,
entitled, and effective state. The same manifest is available at
`pulsyr://capabilities`; the discovery methods expose the corresponding catalog.

| Family | Tools |
|--------|-------|
| Core / Backlog (18, always enabled) | `pulsyr_capabilities`, `pulsyr_context`, `pulsyr_search`, `pulsyr_list`, `pulsyr_areas`, `pulsyr_move_area`, `pulsyr_create`, `pulsyr_advance`, `pulsyr_complete`, `pulsyr_link`, `pulsyr_item_get`, `pulsyr_item_update`, `pulsyr_discard`, `pulsyr_reopen`, `pulsyr_comment_add`, `pulsyr_unlink`, `pulsyr_priority_view`, `pulsyr_archive_list` |
| Threads (7) | `pulsyr_thread_create`, `pulsyr_thread_advance`, `pulsyr_thread_list`, `pulsyr_thread`, `pulsyr_thread_link`, `pulsyr_thread_set_stage`, `pulsyr_thread_artifact_add` |
| Incidents (7) | `pulsyr_incidents`, `pulsyr_incident`, `pulsyr_incident_resolve`, `pulsyr_incident_promote`, `pulsyr_incident_ignore`, `pulsyr_incident_unignore`, `pulsyr_incident_backfill` |
| Management (13) | `pulsyr_doc_list`, `pulsyr_doc_get`, `pulsyr_doc_put`, `pulsyr_doc_rollback`, `pulsyr_doc_update`, `pulsyr_compartment_upsert`, `pulsyr_pending_list`, `pulsyr_pending_upsert`, `pulsyr_pending_complete`, `pulsyr_pending_delete`, `pulsyr_gantt_get`, `pulsyr_gantt_task_upsert`, `pulsyr_gantt_task_remove` |

The 26 original tool names and their required arguments remain compatible. New lifecycle
operations use the same domain services as the UI/REST paths. Mutations are audited;
incident changes use append-only `sentry_issue_events`. Destructive tools are marked in
their MCP annotations, and idempotent operations explicitly advertise that property.

Prompts: `briefing`, `decision`. Concrete resources: `pulsyr://capabilities`,
`pulsyr://incidents/open`, `pulsyr://management/status`. Resource templates:
`pulsyr://area/{name}`, `pulsyr://graph/{item_id}`, `pulsyr://threads/{thread_id}`.

Tool failures return a stable JSON payload inside the MCP error content:
`{"error":{"code":"...","message":"...","details":{...}}}`. Clients should branch
on `code`, not parse `message`. Expected codes include `module_disabled`,
`write_scope_required`, `invalid_argument`, `not_found`, `conflict`, and `internal_error`.

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
