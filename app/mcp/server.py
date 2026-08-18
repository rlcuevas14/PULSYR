"""MCP-over-HTTP endpoint for Pulsyr (Streamable HTTP, JSON mode).

Implements the MCP 2025-03-26 subset that HTTP clients use over plain
request/response (no SSE): initialize, tools/list, tools/call, prompts, resources.
Client-agnostic by construction: Claude Code, Codex CLI, Grok CLI and Cursor all
speak to this same endpoint with a Bearer token.
Bearer auth required; write tools require scope='write'.
Every token must have a project_id — tools fail-safe if not.
"""

import json
import logging
from typing import Any, Callable, Literal

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import ApiToken
from app.auth.rate_limit import limit_client
from app.auth.service import verify_api_token
from app.config import settings
from app.database import get_db
from app.enums import (
    DELIVERABLE_STATUSES,
    DELIVERABLE_TYPES,
    EFFORTS,
    ITEM_STATUSES,
    ITEM_TYPES,
    LIST_ORDERS,
    ORIGENES,
    PENDING_STATUSES,
    RELATIONS,
    SENTRY_STATUSES,
    TERMINAL,
    THREAD_STAGES,
)
from app.mcp import tools
from app.metrics import mcp_tool_finished
from app.projects.modules import ModuleDisabled, enabled_modules, require_module

logger = logging.getLogger("pulsyr.mcp")

PROTOCOL_VERSION = "2025-03-26"

# Non-terminal statuses — targets for pulsyr_advance; terminals go via pulsyr_complete.
_ADVANCE_STATUSES: tuple[str, ...] = tuple(s for s in ITEM_STATUSES if s not in TERMINAL)
_INCIDENT_STATUSES: tuple[str, ...] = tuple(SENTRY_STATUSES) + ("all",)


_CONSTRAINT_HELP: dict[str, str] = {
    "items_type_check": f"invalid type; use one of: {', '.join(ITEM_TYPES)}",
    "items_status_check": f"invalid status; use one of: {', '.join(ITEM_STATUSES)}",
    "items_origen_check": f"invalid origin; use one of: {', '.join(ORIGENES)}",
    "items_effort_ai_check": f"invalid effort_ai; use one of: {', '.join(EFFORTS)} (or null)",
    "items_priority_check": "invalid priority; use one of: p0, p1, p2, p3 (or null)",
    "item_comments_kind_check": (
        "invalid comment kind; use one of: "
        f"{', '.join(('comment', 'ai-analysis', 'decision', 'status-change'))}"
    ),
    "item_relationships_relation_check": f"invalid relation; use one of: {', '.join(RELATIONS)}",
    "item_rel_no_self": "an item cannot be related to itself (source and target are the same)",
    "threads_stage_check": f"invalid thread stage; use one of: {', '.join(THREAD_STAGES)}",
    "thread_artifacts_stage_check": (
        f"invalid artifact stage; use one of: {', '.join(THREAD_STAGES)}"
    ),
    "thread_artifacts_kind_check": (
        "invalid artifact kind; use one of: research, stories, spec, notes, decision"
    ),
    "scopes_name_key": "an area with that name already exists (area names are unique per project)",
    "deliverables_doc_type_check": f"invalid doc_type; use one of: {', '.join(DELIVERABLE_TYPES)}",
    "deliverables_status_check": (
        f"invalid deliverable status; use one of: {', '.join(DELIVERABLE_STATUSES)}"
    ),
    "deliverables_compartment_name_uniq": (
        "a deliverable with that name already exists in this compartment"
    ),
    "compartments_project_name_uniq": "a compartment with that name already exists in this project",
    "pendings_status_check": f"invalid pending status; use one of: {', '.join(PENDING_STATUSES)}",
    "plan_tasks_progress_check": "progress must be between 0 and 100",
}


def _humanize_integrity_error(e: IntegrityError) -> str:
    detail = str(getattr(e, "orig", e)) or str(e)
    for constraint, help_text in _CONSTRAINT_HELP.items():
        if constraint in detail:
            return f"Constraint violation ({constraint}): {help_text}."
    low = detail.lower()
    if "foreign key" in low or "llave foránea" in low:
        return "Invalid reference: points to a row that does not exist."
    return "Database constraint rejected this mutation."


ModuleName = Literal["core", "threads", "incidents", "management"]


class Tool:
    def __init__(
        self,
        name: str,
        description: str,
        schema: dict,
        handler: Callable,
        write: bool,
        *,
        module: ModuleName = "core",
        destructive: bool = False,
        idempotent: bool = False,
        open_world: bool = False,
    ):
        self.name = name
        self.description = description
        self.schema = schema
        self.handler = handler
        self.write = write
        self.module = module
        self.destructive = destructive
        self.idempotent = idempotent
        self.open_world = open_world

    @property
    def annotations(self) -> dict[str, bool]:
        return {
            "readOnlyHint": not self.write,
            "destructiveHint": self.destructive,
            "idempotentHint": self.idempotent,
            "openWorldHint": self.open_world,
        }


def _scope_obj(props: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


def _enum(values: tuple[str, ...] | list[str], description: str | None = None) -> dict:
    schema: dict[str, Any] = {"type": "string", "enum": list(values)}
    if description:
        schema["description"] = description
    return schema


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


_STR = {"type": "string"}
_INT = {"type": "integer"}

TOOLS: dict[str, Tool] = {
    "pulsyr_capabilities": Tool(
        "pulsyr_capabilities",
        "Project capability manifest: configured, entitled, and effective modules.",
        _scope_obj({}, []),
        tools.pulsyr_capabilities,
        write=False,
    ),
    "pulsyr_context": Tool(
        "pulsyr_context",
        "Session start summary: quick wins, blockers, unlinked Sentry bugs, active threads, "
        "graph neighborhood, and (if embeddings are available) semantically similar items.",
        _scope_obj({"area": _STR, "work_description": _STR}, []),
        tools.pulsyr_context, write=False,
    ),
    "pulsyr_search": Tool(
        "pulsyr_search", "Full-text search across backlog items.",
        _scope_obj({"q": _STR, "area": {**_STR, "description": "area name to filter by"},
                    "type": _enum(ITEM_TYPES, "filter by item type"), "limit": _INT}, ["q"]),
        tools.pulsyr_search, write=False,
    ),
    "pulsyr_list": Tool(
        "pulsyr_list",
        "Filtered item list. order: impact|priority|topological|recent. quickwins: bool.",
        _scope_obj({"area": _STR,
                    "status": {"type": "array", "items": _enum(ITEM_STATUSES),
                               "description": "statuses to include (all if omitted)"},
                    "type": _enum(ITEM_TYPES, "filter by item type"),
                    "order": _enum(LIST_ORDERS, "sort order (default impact)"),
                    "quickwins": {"type": "boolean"},
                    "limit": _INT}, []),
        tools.pulsyr_list, write=False,
    ),
    "pulsyr_areas": Tool(
        "pulsyr_areas",
        "List areas (backlog groupings) with name, description, item count, and examples. "
        "Call this before creating an item to pick the right area.",
        _scope_obj({}, []),
        tools.pulsyr_areas, write=False,
    ),
    "pulsyr_move_area": Tool(
        "pulsyr_move_area",
        "Move an item to a different existing area (fixes miscategorization). "
        "Accepts item_id or text query.",
        _scope_obj({"item_id": _STR, "query": _STR, "area_name": _STR}, ["area_name"]),
        tools.pulsyr_move_area, write=True,
    ),
    "pulsyr_create": Tool(
        "pulsyr_create",
        "Create a backlog item (status backlog, origin ai-session by default). "
        "Creates the area if it doesn't exist. thread_id (optional): links it to a Thread. "
        "Always send effort_ai and impact_ai: you have just read the code, so your estimate "
        "is better informed than the server-side one, which only ever sees title + summary. "
        "An item without impact_ai has no place in the priority matrix until someone pays "
        "for enrichment to backfill it.",
        _scope_obj({"title": _STR, "summary": _STR,
                    "type": _enum(ITEM_TYPES, "item type"),
                    "area_name": _STR,
                    "effort_ai": _enum(EFFORTS, "estimated effort — estimate it from the code "
                                                "you just read; do not leave it null"),
                    "impact_ai": {**_INT, "description": "impact 1-5 — estimate it yourself; "
                                                         "without it the item is unprioritized",
                                  "minimum": 1, "maximum": 5},
                    "origin": _enum(ORIGENES, "item origin (default ai-session)"),
                    "thread_id": _STR},
                   ["title", "type", "area_name"]),
        tools.pulsyr_create, write=True,
    ),
    "pulsyr_advance": Tool(
        "pulsyr_advance",
        "Change item status (validated transition; terminals go via pulsyr_complete). "
        "Accepts item_id or text query.",
        _scope_obj({"item_id": _STR, "query": _STR,
                    "to_status": _enum(_ADVANCE_STATUSES,
                                       "target status (non-terminal; close with pulsyr_complete)")},
                   ["to_status"]),
        tools.pulsyr_advance, write=True,
    ),
    "pulsyr_complete": Tool(
        "pulsyr_complete",
        "Mark an item as done (with optional note and commit_sha). Reports newly unblocked items. "
        "Accepts item_id or search_query (aborts if ambiguous).",
        _scope_obj({"item_id": _STR, "search_query": _STR, "note": _STR, "commit_sha": _STR}, []),
        tools.pulsyr_complete, write=True,
    ),
    "pulsyr_link": Tool(
        "pulsyr_link",
        "Create a graph edge between two items. relation: blocks|requires|conflicts|related|part_of. "
        "Accepts ids or text queries.",
        _scope_obj({"source_id": _STR, "source_query": _STR, "target_id": _STR,
                    "target_query": _STR,
                    "relation": _enum(RELATIONS, "edge type"),
                    "note": _STR}, ["relation"]),
        tools.pulsyr_link, write=True, idempotent=True,
    ),
    "pulsyr_item_get": Tool(
        "pulsyr_item_get",
        "Item detail with events, comments, enrichments, and optional relationship graph.",
        _scope_obj({"item_id": _STR, "query": _STR, "include_graph": {"type": "boolean"}}, []),
        tools.pulsyr_item_get, write=False,
    ),
    "pulsyr_item_update": Tool(
        "pulsyr_item_update",
        "Update non-lifecycle item fields. Use advance/complete/discard/reopen for status.",
        _scope_obj({
            "item_id": _STR, "query": _STR,
            "title": _STR, "summary": _nullable(_STR),
            "priority": _nullable(_enum(["p0", "p1", "p2", "p3"])),
            "impact_ai": _nullable({**_INT, "minimum": 1, "maximum": 5}),
            "effort_ai": _nullable(_enum(EFFORTS)),
            "stale_risk": {"type": "boolean"}, "agent_ready": {"type": "boolean"},
        }, []),
        tools.pulsyr_item_update, write=True,
    ),
    "pulsyr_discard": Tool(
        "pulsyr_discard",
        "Discard an item with a mandatory reason.",
        _scope_obj({"item_id": _STR, "query": _STR, "reason": _STR}, ["reason"]),
        tools.pulsyr_discard, write=True, destructive=True,
    ),
    "pulsyr_reopen": Tool(
        "pulsyr_reopen",
        "Reopen a done or discarded item into backlog.",
        _scope_obj({"item_id": _STR, "query": _STR}, []),
        tools.pulsyr_reopen, write=True,
    ),
    "pulsyr_comment_add": Tool(
        "pulsyr_comment_add",
        "Append an immutable item comment.",
        _scope_obj({
            "item_id": _STR, "query": _STR, "body_md": _STR,
            "kind": _enum(["comment", "ai-analysis", "decision", "status-change"]),
        }, ["body_md"]),
        tools.pulsyr_comment_add, write=True,
    ),
    "pulsyr_unlink": Tool(
        "pulsyr_unlink",
        "Remove an item relationship; repeated removal is a no-op.",
        _scope_obj({
            "source_id": _STR, "source_query": _STR,
            "target_id": _STR, "target_query": _STR,
            "relation": _enum(RELATIONS),
        }, ["relation"]),
        tools.pulsyr_unlink, write=True, destructive=True, idempotent=True,
    ),
    "pulsyr_priority_view": Tool(
        "pulsyr_priority_view",
        "Priority matrix projection with ordered items and an explicit unestimated group.",
        _scope_obj({"area": _STR, "type": _enum(ITEM_TYPES)}, []),
        tools.pulsyr_priority_view, write=False,
    ),
    "pulsyr_archive_list": Tool(
        "pulsyr_archive_list",
        "Paginated closed work by ISO week, including close reason and commit receipt.",
        _scope_obj({
            "week": {**_STR, "pattern": r"^\d{4}-W\d{2}$"},
            "status": _enum(["done", "discarded"]),
            "area": _STR, "type": _enum(ITEM_TYPES), "limit": _INT, "offset": _INT,
        }, []),
        tools.pulsyr_archive_list, write=False,
    ),
    "pulsyr_thread_create": Tool(
        "pulsyr_thread_create", "Create a Thread (heavy feature) at stage idea.",
        _scope_obj({"title": _STR, "summary": _STR, "area_name": _STR}, ["title", "area_name"]),
        tools.pulsyr_thread_create, write=True, module="threads",
    ),
    "pulsyr_thread_advance": Tool(
        "pulsyr_thread_advance",
        "Advance a Thread to the next stage; optionally saves an artifact "
        "{stage, content} from the current stage.",
        _scope_obj({"thread_id": _STR, "artifact": {"type": "object"}}, ["thread_id"]),
        tools.pulsyr_thread_advance, write=True, module="threads",
    ),
    "pulsyr_thread_list": Tool(
        "pulsyr_thread_list", "List Threads (optional filter by stage and area).",
        _scope_obj({"stage": _enum(THREAD_STAGES, "filter by thread stage"),
                    "area": _STR, "limit": _INT}, []),
        tools.pulsyr_thread_list, write=False, module="threads",
    ),
    "pulsyr_thread": Tool(
        "pulsyr_thread", "Thread detail: stage, artifacts, and linked items.",
        _scope_obj({"id": _STR}, ["id"]),
        tools.pulsyr_thread, write=False, module="threads",
    ),
    "pulsyr_thread_link": Tool(
        "pulsyr_thread_link",
        "Link an existing item to a Thread (sets thread_id). "
        "Accepts item_id or text query, and thread_id.",
        _scope_obj({"thread_id": _STR, "item_id": _STR, "query": _STR}, ["thread_id"]),
        tools.pulsyr_thread_link, write=True, module="threads",
    ),
    "pulsyr_thread_set_stage": Tool(
        "pulsyr_thread_set_stage",
        "Explicitly set a Thread stage, including backwards moves; done keeps the open-item guard.",
        _scope_obj({"thread_id": _STR, "stage": _enum(THREAD_STAGES)}, ["thread_id", "stage"]),
        tools.pulsyr_thread_set_stage, write=True, module="threads",
    ),
    "pulsyr_thread_artifact_add": Tool(
        "pulsyr_thread_artifact_add",
        "Append a Thread artifact without advancing its stage.",
        _scope_obj({
            "thread_id": _STR,
            "kind": _enum(["research", "stories", "spec", "notes", "decision"]),
            "content": _STR, "stage": _enum(THREAD_STAGES),
        }, ["thread_id", "kind", "content"]),
        tools.pulsyr_thread_artifact_add, write=True, module="threads",
    ),
    "pulsyr_incidents": Tool(
        "pulsyr_incidents",
        "List Sentry errors in the incident container. status: new|linked|resolved|ignored|all "
        "(default new).",
        _scope_obj({"status": _enum(_INCIDENT_STATUSES, "filter by status (default new)"),
                    "limit": _INT}, []),
        tools.pulsyr_incidents, write=False, module="incidents",
    ),
    "pulsyr_incident": Tool(
        "pulsyr_incident",
        "Incident detail WITH stack trace (exception, file:line, code) fetched from Sentry — "
        "what you need to locate and fix the error. id = incident id.",
        _scope_obj({"id": _STR}, ["id"]),
        tools.pulsyr_incident, write=False, module="incidents", open_world=True,
    ),
    "pulsyr_incident_resolve": Tool(
        "pulsyr_incident_resolve",
        "Mark an incident as resolved in Pulsyr and (by default) in Sentry. "
        "Use after fixing the bug. resolve_in_sentry: bool (default true).",
        _scope_obj({"id": _STR, "note": _STR, "commit_sha": _STR,
                    "resolve_in_sentry": {"type": "boolean"}}, ["id"]),
        tools.pulsyr_incident_resolve, write=True, module="incidents", open_world=True,
    ),
    "pulsyr_incident_promote": Tool(
        "pulsyr_incident_promote",
        "Promote an incident to a backlog bug; returns the existing item if already linked.",
        _scope_obj({"id": _STR, "priority": _enum(["p0", "p1", "p2", "p3"])}, ["id"]),
        tools.pulsyr_incident_promote,
        write=True, module="incidents", idempotent=True,
    ),
    "pulsyr_incident_ignore": Tool(
        "pulsyr_incident_ignore",
        "Ignore a new incident with an optional audit reason.",
        _scope_obj({"id": _STR, "reason": _STR}, ["id"]),
        tools.pulsyr_incident_ignore,
        write=True, module="incidents", idempotent=True,
    ),
    "pulsyr_incident_unignore": Tool(
        "pulsyr_incident_unignore",
        "Restore an ignored incident to new.",
        _scope_obj({"id": _STR}, ["id"]),
        tools.pulsyr_incident_unignore,
        write=True, module="incidents", idempotent=True,
    ),
    "pulsyr_incident_backfill": Tool(
        "pulsyr_incident_backfill",
        "Import bounded Sentry history using the server-side project connection.",
        _scope_obj({"query": _STR, "limit": {**_INT, "minimum": 1, "maximum": 100}}, []),
        tools.pulsyr_incident_backfill,
        write=True, module="incidents", idempotent=True, open_world=True,
    ),
    # ----- Management: documentos -----
    "pulsyr_doc_list": Tool(
        "pulsyr_doc_list",
        "List deliverables (documents) in the Management tab. Metadata only (no bytes). "
        "Filter by compartment_id, status, or q (name/summary substring).",
        _scope_obj({"compartment_id": _STR,
                    "status": _enum(DELIVERABLE_STATUSES, "filter by status"),
                    "q": {**_STR, "description": "search name/summary"}}, []),
        tools.pulsyr_doc_list, write=False, module="management",
    ),
    "pulsyr_doc_get": Tool(
        "pulsyr_doc_get",
        "Deliverable detail: metadata + version history. include_content=true inlines the "
        "current version (text for md/html, base64 for binary) up to 256 KB; larger → download via UI.",
        _scope_obj({"deliverable_id": _STR, "include_content": {"type": "boolean"}},
                   ["deliverable_id"]),
        tools.pulsyr_doc_get, write=False, module="management",
    ),
    "pulsyr_doc_put": Tool(
        "pulsyr_doc_put",
        "Create a deliverable or append a new version (append-only; identical bytes are a no-op). "
        "Auto-creates the compartment. Pass content (text) for md/html or content_base64 (binary). "
        "doc_type ∈ docx|pdf|html|md|xlsx|pptx. Max 10 MB.",
        _scope_obj({"compartment": _STR, "name": _STR,
                    "doc_type": _enum(DELIVERABLE_TYPES, "deliverable type"),
                    "content": {**_STR, "description": "raw text (md/html)"},
                    "content_base64": {**_STR, "description": "base64 bytes (any type)"},
                    "summary_md": {**_STR, "description": "short summary for search/preview"},
                    "status": _enum(DELIVERABLE_STATUSES, "status (default draft)"),
                    "owner": _STR, "note": {**_STR, "description": "what changed in this version"}},
                   ["compartment", "name", "doc_type"]),
        tools.pulsyr_doc_put, write=True, module="management", idempotent=True,
    ),
    "pulsyr_doc_rollback": Tool(
        "pulsyr_doc_rollback",
        "Copy a prior deliverable version into a new append-only current version.",
        _scope_obj({"deliverable_id": _STR, "version_no": _INT}, ["deliverable_id", "version_no"]),
        tools.pulsyr_doc_rollback, write=True, module="management",
    ),
    "pulsyr_doc_update": Tool(
        "pulsyr_doc_update",
        "Update deliverable metadata without creating a content version.",
        _scope_obj({
            "deliverable_id": _STR, "name": _STR,
            "summary_md": _nullable(_STR), "owner": _nullable(_STR),
            "status": _enum(DELIVERABLE_STATUSES), "compartment_id": _STR,
        }, ["deliverable_id"]),
        tools.pulsyr_doc_update, write=True, module="management",
    ),
    "pulsyr_compartment_upsert": Tool(
        "pulsyr_compartment_upsert",
        "Create or update a document compartment and its metadata.",
        _scope_obj({
            "compartment_id": _STR, "name": _STR,
            "description": _nullable(_STR), "sort_order": _INT,
        }, []),
        tools.pulsyr_compartment_upsert,
        write=True, module="management", idempotent=True,
    ),
    # ----- Management: pendientes -----
    "pulsyr_pending_list": Tool(
        "pulsyr_pending_list",
        "List project pendings (action items) with owner + status. Filter by status, owner, "
        "overdue (bool), or plan_task_id.",
        _scope_obj({"status": _enum(PENDING_STATUSES, "filter by status"),
                    "owner": _STR, "overdue": {"type": "boolean"}, "plan_task_id": _STR}, []),
        tools.pulsyr_pending_list, write=False, module="management",
    ),
    "pulsyr_pending_upsert": Tool(
        "pulsyr_pending_upsert",
        "Create or update a pending. Omit pending_id to create (title required). "
        "status ∈ open|doing|blocked|done. due_date is ISO YYYY-MM-DD. "
        "plan_task_id links it to a Gantt task.",
        _scope_obj({"pending_id": _STR, "title": _STR, "detail_md": _STR, "owner": _STR,
                    "status": _enum(PENDING_STATUSES, "status"),
                    "due_date": {**_STR, "description": "ISO date YYYY-MM-DD"},
                    "plan_task_id": _STR}, []),
        tools.pulsyr_pending_upsert, write=True, module="management",
    ),
    "pulsyr_pending_complete": Tool(
        "pulsyr_pending_complete", "Mark a pending as done (sets closed_at).",
        _scope_obj({"pending_id": _STR}, ["pending_id"]),
        tools.pulsyr_pending_complete, write=True, module="management",
    ),
    "pulsyr_pending_delete": Tool(
        "pulsyr_pending_delete",
        "Delete a pending; repeated deletion is a no-op.",
        _scope_obj({"pending_id": _STR}, ["pending_id"]),
        tools.pulsyr_pending_delete,
        write=True, module="management", destructive=True, idempotent=True,
    ),
    # ----- Management: gantt (plan) -----
    "pulsyr_gantt_get": Tool(
        "pulsyr_gantt_get",
        "Read the project plan (Gantt): all tasks with hierarchy (parent_id), dates, progress, "
        "milestones, and deps, plus the plan's start/end bounds. Read this before editing.",
        _scope_obj({}, []),
        tools.pulsyr_gantt_get, write=False, module="management",
    ),
    "pulsyr_gantt_task_upsert": Tool(
        "pulsyr_gantt_task_upsert",
        "Create or update a Gantt task. Omit task_id to create (name required). parent_id nests "
        "it (max 3 levels: phase/sub-phase/task). Dates are ISO YYYY-MM-DD; progress 0-100; "
        "is_milestone renders a diamond at start_date; deps is a list of predecessor task ids; "
        "sort_order orders siblings.",
        _scope_obj({"task_id": _STR, "name": _STR, "parent_id": _STR,
                    "start_date": {**_STR, "description": "ISO date"},
                    "end_date": {**_STR, "description": "ISO date"},
                    "progress": {**_INT, "description": "0-100", "minimum": 0, "maximum": 100},
                    "is_milestone": {"type": "boolean"},
                    "deps": {"type": "array", "items": _STR, "description": "predecessor task ids"},
                    "sort_order": _INT}, []),
        tools.pulsyr_gantt_task_upsert, write=True, module="management",
    ),
    "pulsyr_gantt_task_remove": Tool(
        "pulsyr_gantt_task_remove",
        "Delete a Gantt task (its children cascade).",
        _scope_obj({"task_id": _STR}, ["task_id"]),
        tools.pulsyr_gantt_task_remove, write=True, module="management", destructive=True,
    ),
}

PROMPTS: dict[str, dict[str, Any]] = {
    "briefing": {
        "module": "core",
        "descriptor": {
            "name": "briefing",
            "description": "Session start context (priorities, blockers, neighborhood).",
            "arguments": [
                {"name": "area", "description": "active area", "required": False},
                {"name": "work_description", "description": "what you are working on", "required": False},
            ],
        },
    },
    "decision": {
        "module": "core",
        "descriptor": {
            "name": "decision",
            "description": "Recorded architecture decisions (item_comments kind=decision).",
            "arguments": [
                {"name": "topic", "description": "topic to search for", "required": True}
            ],
        },
    },
}

RESOURCE_TEMPLATES: list[dict[str, Any]] = [
    {"module": "core", "prefix": "pulsyr://area/", "descriptor": {
        "uriTemplate": "pulsyr://area/{area_name}", "name": "area",
        "description": "Area view: items by status.", "mimeType": "application/json",
    }},
    {"module": "core", "prefix": "pulsyr://graph/", "descriptor": {
        "uriTemplate": "pulsyr://graph/{item_id}", "name": "graph",
        "description": "Item relationship subgraph.", "mimeType": "application/json",
    }},
    {"module": "threads", "prefix": "pulsyr://threads/", "descriptor": {
        "uriTemplate": "pulsyr://threads/{thread_id}", "name": "thread",
        "description": "Thread detail, artifacts, and linked items.",
        "mimeType": "application/json",
    }},
]

RESOURCES: list[dict[str, Any]] = [
    {"module": "core", "descriptor": {
        "uri": "pulsyr://capabilities", "name": "capabilities",
        "description": "Project capability manifest.", "mimeType": "application/json",
    }},
    {"module": "incidents", "descriptor": {
        "uri": "pulsyr://incidents/open", "name": "open incidents",
        "description": "Open incident container.", "mimeType": "application/json",
    }},
    {"module": "management", "descriptor": {
        "uri": "pulsyr://management/status", "name": "management status",
        "description": "Document, pending, and plan counts.", "mimeType": "application/json",
    }},
]


def _err(rpc_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def _ok(rpc_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _tool_result(payload: Any, is_error: bool = False) -> dict:
    text_out = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text_out}], "isError": is_error}


def _error_payload(code: str, message: str, details: dict[str, Any] | None = None) -> dict:
    return {
        "schema_version": 1,
        "error": {"code": code, "message": message, "details": details or {}},
    }


def _tool_error(code: str, message: str, details: dict[str, Any] | None = None) -> dict:
    return _tool_result(_error_payload(code, message, details), is_error=True)


def _visible(module: str, modules: frozenset[str]) -> bool:
    return module == "core" or module in modules


async def _dispatch(msg: dict, token: ApiToken, db: AsyncSession) -> dict | None:
    method = msg.get("method")
    rpc_id = msg.get("id")
    params = msg.get("params") or {}

    # Notifications (no id) get no response.
    if rpc_id is None:
        return None

    if token.project_id is None:
        return _err(
            rpc_id,
            -32001,
            "Token has no project assigned. Generate it from a project's Settings page.",
        )

    if method == "initialize":
        modules = await enabled_modules(db, token.project_id)
        optional = ", ".join(sorted(modules - {"core"})) or "none"
        return _ok(rpc_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}, "prompts": {}, "resources": {}},
            "serverInfo": {"name": "pulsyr", "version": "2.0"},
            "instructions": (
                "Pulsyr project workspace. Call pulsyr_context at session start and "
                "pulsyr_complete when closing out an item. Optional modules enabled: "
                f"{optional}. Call pulsyr_capabilities to explain unavailable families."
            ),
        })

    if method == "ping":
        return _ok(rpc_id, {})

    if method == "tools/list":
        modules = await enabled_modules(db, token.project_id)
        return _ok(rpc_id, {"tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.schema,
                "annotations": tool.annotations,
            }
            for tool in TOOLS.values() if _visible(tool.module, modules)
        ]})

    if method == "tools/call":
        name = params.get("name") or ""
        arguments = params.get("arguments") or {}
        tool = TOOLS.get(name)
        if tool is None:
            return _ok(rpc_id, _tool_error(
                "unknown_tool", f"Unknown tool: {name}", {"tool": name}
            ))
        try:
            await require_module(db, token.project_id, tool.module)
        except ModuleDisabled:
            mcp_tool_finished(tool.module, "module_disabled")
            return _ok(rpc_id, _tool_error(
                "module_disabled",
                f"The {tool.module} module is disabled for this project.",
                {"module": tool.module},
            ))
        if tool.write and token.scopes != "write":
            mcp_tool_finished(tool.module, "write_scope_required")
            return _ok(rpc_id, _tool_error(
                "write_scope_required",
                f"Tool '{name}' requires scope 'write'.",
                {"required_scope": "write", "token_scope": token.scopes},
            ))
        try:
            result = await tool.handler(db, token, arguments)
            await db.commit()
            mcp_tool_finished(tool.module, "ok")
            return _ok(rpc_id, _tool_result(result))
        except tools.ToolError as e:
            await db.rollback()
            mcp_tool_finished(tool.module, e.code)
            return _ok(rpc_id, _tool_error(e.code, str(e), e.details))
        except KeyError as e:
            await db.rollback()
            mcp_tool_finished(tool.module, "invalid_argument")
            return _ok(rpc_id, _tool_error(
                "invalid_argument", f"Missing required argument: {e}"
            ))
        except IntegrityError as e:
            await db.rollback()
            mcp_tool_finished(tool.module, "conflict")
            logger.warning("tool %s module=%s integrity error", name, tool.module)
            return _ok(rpc_id, _tool_error(
                "conflict", _humanize_integrity_error(e)
            ))
        except (ValueError, LookupError) as e:
            await db.rollback()
            mcp_tool_finished(tool.module, "invalid_argument")
            logger.warning("tool %s module=%s invalid argument", name, tool.module)
            return _ok(rpc_id, _tool_error("invalid_argument", str(e)))
        except Exception:  # safety net: never let /mcp return HTTP 500
            await db.rollback()
            mcp_tool_finished(tool.module, "internal_error")
            logger.exception("tool %s module=%s failed", name, tool.module)
            return _ok(rpc_id, _tool_error(
                "internal_error", f"Tool '{name}' could not complete safely."
            ))

    if method == "prompts/list":
        modules = await enabled_modules(db, token.project_id)
        return _ok(rpc_id, {"prompts": [
            entry["descriptor"] for entry in PROMPTS.values()
            if _visible(entry["module"], modules)
        ]})

    if method == "prompts/get":
        return await _prompt_get(rpc_id, params, token, db)

    if method == "resources/list":
        modules = await enabled_modules(db, token.project_id)
        return _ok(rpc_id, {"resources": [
            entry["descriptor"] for entry in RESOURCES
            if _visible(entry["module"], modules)
        ]})

    if method == "resources/templates/list":
        modules = await enabled_modules(db, token.project_id)
        return _ok(rpc_id, {"resourceTemplates": [
            entry["descriptor"] for entry in RESOURCE_TEMPLATES
            if _visible(entry["module"], modules)
        ]})

    if method == "resources/read":
        return await _resource_read(rpc_id, params, token, db)

    return _err(rpc_id, -32601, f"Unsupported method: {method}")


async def _prompt_get(rpc_id: Any, params: dict, token: ApiToken, db: AsyncSession) -> dict:
    name = str(params.get("name") or "")
    args = params.get("arguments") or {}
    entry = PROMPTS.get(name)
    if entry is None:
        return _err(rpc_id, -32602, f"Unknown prompt: {name}")
    assert token.project_id is not None
    try:
        await require_module(db, token.project_id, entry["module"])
    except ModuleDisabled:
        return _err(
            rpc_id,
            -32003,
            json.dumps(_error_payload(
                "module_disabled",
                f"The {entry['module']} module is disabled for this project.",
                {"module": entry["module"]},
            )),
        )
    if name == "briefing":
        ctx = await tools.pulsyr_context(db, token, args)
        body = json.dumps(ctx, ensure_ascii=False, indent=2)
        text_out = f"Pulsyr session context:\n{body}"
    elif name == "decision":
        topic = args.get("topic", "")
        from sqlalchemy import text
        pid_filter = "AND i.project_id = :pid" if token.project_id else ""
        rows = (await db.execute(text(f"""
            SELECT c.body_md, c.author, i.title
            FROM item_comments c JOIN items i ON i.id = c.item_id
            WHERE c.kind = 'decision' AND c.body_md ILIKE :t {pid_filter}
            ORDER BY c.created_at DESC LIMIT 10
        """), {"t": f"%{topic}%", "pid": token.project_id})).mappings().all()
        if rows:
            text_out = "Recorded decisions:\n" + "\n".join(
                f"- ({r['title']}, {r['author']}) {r['body_md']}" for r in rows)
        else:
            text_out = f"No recorded decisions about '{topic}'."
    return _ok(rpc_id, {"messages": [{"role": "user", "content": {"type": "text", "text": text_out}}]})


async def _resource_read(rpc_id: Any, params: dict, token: ApiToken, db: AsyncSession) -> dict:
    from sqlalchemy import text
    uri = params.get("uri", "")
    pid = token.project_id
    assert pid is not None
    module: str | None = None
    if uri == "pulsyr://capabilities" or uri.startswith(("pulsyr://area/", "pulsyr://graph/")):
        module = "core"
    elif uri.startswith("pulsyr://threads/"):
        module = "threads"
    elif uri == "pulsyr://incidents/open":
        module = "incidents"
    elif uri == "pulsyr://management/status":
        module = "management"
    if module is None:
        return _err(rpc_id, -32602, f"Unsupported URI: {uri}")
    try:
        await require_module(db, pid, module)
    except ModuleDisabled:
        return _err(
            rpc_id,
            -32003,
            json.dumps(_error_payload(
                "module_disabled",
                f"The {module} module is disabled for this project.",
                {"module": module},
            )),
        )
    payload: Any
    if uri == "pulsyr://capabilities":
        payload = await tools.capabilities_payload(db, token)
    elif uri.startswith("pulsyr://area/"):
        name = uri.split("/", 3)[-1]
        rows = (await db.execute(text("""
            SELECT i.status, count(*) AS n FROM items i JOIN scopes s ON s.id = i.scope_id
            WHERE s.name = :name AND s.project_id = :pid GROUP BY i.status
        """), {"name": name, "pid": pid})).mappings().all()
        payload = {"area": name, "counts": {r["status"]: r["n"] for r in rows}}
    elif uri.startswith("pulsyr://graph/"):
        import uuid as _uuid

        from app.items import graph
        from app.items.models import Item

        item_id = uri.split("/", 3)[-1]
        try:
            parsed = _uuid.UUID(item_id)
        except ValueError:
            return _err(rpc_id, -32602, "Invalid item id in graph URI")
        item = await db.get(Item, parsed)
        if item is None or item.project_id != pid:
            return _err(rpc_id, -32602, "Item not found in this project")
        payload = await graph.subgraph(db, parsed, project_id=pid)
    elif uri.startswith("pulsyr://threads/"):
        thread_id = uri.split("/", 3)[-1]
        try:
            payload = await tools.pulsyr_thread(db, token, {"id": thread_id})
        except tools.ToolError:
            return _err(rpc_id, -32602, "Thread not found in this project")
    elif uri == "pulsyr://incidents/open":
        rows = (await db.execute(text("""
            SELECT id, sentry_issue_id, title, level, status, events_count
            FROM sentry_issues
            WHERE project_id = :pid AND status IN ('new', 'linked')
            ORDER BY last_seen DESC NULLS LAST LIMIT 100
        """), {"pid": pid})).mappings().all()
        payload = {"incidents": [dict(row) for row in rows]}
        for incident in payload["incidents"]:
            incident["id"] = str(incident["id"])
    else:
        counts = (await db.execute(text("""
            SELECT
                (SELECT count(*) FROM deliverables WHERE project_id = :pid) AS documents,
                (SELECT count(*) FROM pendings
                 WHERE project_id = :pid AND status <> 'done') AS open_pendings,
                (SELECT count(*) FROM plan_tasks WHERE project_id = :pid) AS plan_tasks
        """), {"pid": pid})).mappings().one()
        payload = dict(counts)
    return _ok(rpc_id, {"contents": [
        {"uri": uri, "mimeType": "application/json", "text": json.dumps(payload, ensure_ascii=False)}
    ]})


def mount_mcp(app: FastAPI) -> None:
    @app.post("/mcp")
    async def mcp_post(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
        decision = await limit_client(
            request,
            action="mcp",
            limit=settings.mcp_rate_limit_attempts,
            window_seconds=settings.machine_rate_limit_window_seconds,
        )
        if not decision.allowed:
            return JSONResponse(
                {"error": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(decision.retry_after)},
            )
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error": "Bearer token required"}, status_code=401)
        token = await verify_api_token(db, auth.split(" ", 1)[1].strip())
        if token is None:
            return JSONResponse({"error": "Invalid or revoked token"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(_err(None, -32700, "Invalid JSON"), status_code=400)

        if isinstance(body, list):
            responses = [r for m in body if (r := await _dispatch(m, token, db)) is not None]
            return JSONResponse(responses) if responses else Response(status_code=202)

        response = await _dispatch(body, token, db)
        if response is None:
            return Response(status_code=202)
        return JSONResponse(response)

    @app.get("/mcp")
    async def mcp_get() -> Response:
        return JSONResponse(
            _err(None, -32600,
                 "The /mcp endpoint only accepts POST (JSON-RPC over HTTP). "
                 "This transport does not expose a server→client SSE stream via GET."),
            status_code=405,
            headers={"Allow": "POST"},
        )
