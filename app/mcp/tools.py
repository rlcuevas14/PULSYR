"""MCP tool implementations for Pulsyr.

Each tool delegates to the service layer (lifecycle, graph, relationships) to avoid
diverging from UI/REST behavior. Business errors propagate as ToolError → isError.
All tools are project-scoped: the token's project_id is the isolation boundary.
"""

import base64
import logging
import uuid
from datetime import date
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import ApiToken, User
from app.enums import EFFORTS, ITEM_TYPES, OPEN_STATUSES, ORIGENES
from app.items import graph, relationships, service
from app.items.lifecycle import valid_transition
from app.items.models import Item
from app.items.search import search_items
from app.management import service as mgmt
from app.scopes import service as scopes_service
from app.scopes.models import Scope

logger = logging.getLogger("pulsyr.mcp.tools")

_OPEN = list(OPEN_STATUSES)


class ToolError(Exception):
    """Business error in a tool (returned as isError, not a JSON-RPC error)."""


def _uuid_or_error(ref: Any, field: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(str(ref))
    except (ValueError, AttributeError, TypeError) as e:
        raise ToolError(f"{field} is not a valid UUID: '{ref}'.") from e


def _pid(token: ApiToken) -> uuid.UUID:
    """Return token's project_id, or raise ToolError if not set."""
    if token.project_id is None:
        raise ToolError(
            "Token has no project assigned. "
            "Create a project at /projects and generate a token from its Settings page."
        )
    return token.project_id


async def actor_for(db: AsyncSession, token: ApiToken) -> str:
    user = (await db.execute(select(User).where(User.id == token.created_by))).scalar_one_or_none()
    return user.email if user else f"token:{token.name}"


async def actor_user_id(db: AsyncSession, token: ApiToken) -> uuid.UUID | None:
    user = (await db.execute(select(User).where(User.id == token.created_by))).scalar_one_or_none()
    return user.id if user else None


async def _resolve_scope(
    db: AsyncSession, name: str, create: bool = False, project_id: uuid.UUID | None = None
) -> Scope:
    try:
        return await scopes_service.resolve_scope(
            db, name, create=create, project_id=project_id,
            source_repo="mcp" if create else None,
        )
    except scopes_service.ScopeError as e:
        raise ToolError(str(e)) from e


async def _scope_exists(db: AsyncSession, name: str, project_id: uuid.UUID | None) -> bool:
    cleaned = (name or "").strip()
    if not cleaned:
        return False
    q = select(Scope.id).where(func.lower(Scope.name) == cleaned.lower())
    if project_id is not None:
        q = q.where(Scope.project_id == project_id)
    return (await db.execute(q)).first() is not None


async def _scope_map(db: AsyncSession, project_id: uuid.UUID | None = None) -> dict[str, str]:
    q = select(Scope.id, Scope.name)
    if project_id is not None:
        q = q.where(Scope.project_id == project_id)
    rows = (await db.execute(q)).all()
    return {str(r[0]): r[1] for r in rows}


async def _resolve_item(db: AsyncSession, ref: str, project_id: uuid.UUID | None = None) -> Item:
    try:
        item_id = uuid.UUID(str(ref))
    except (ValueError, AttributeError, TypeError):
        try:
            item_id = await relationships.resolve_query(db, ref)
        except relationships.RelationshipError as e:
            raise ToolError(str(e)) from e
    item = await service.get_item(db, item_id)
    if item is None:
        raise ToolError(f"Item not found: {ref}")
    if project_id is not None and item.project_id != project_id:
        raise ToolError(f"Item not found in this project: {ref}")
    return item


async def _resolve_item_verbose(
    db: AsyncSession,
    item_id: str | None,
    query: str | None,
    *,
    what: str = "item_id or query",
    project_id: uuid.UUID | None = None,
) -> tuple[Item, str | None]:
    if item_id:
        return await _resolve_item(db, item_id, project_id), None
    if not query:
        raise ToolError(f"Provide {what}.")
    try:
        resolved = await relationships.resolve_query_verbose(db, query)
    except relationships.RelationshipError as e:
        raise ToolError(str(e)) from e
    item = await service.get_item(db, resolved["id"])
    if item is None:
        raise ToolError(f"Item not found: {query}")
    if project_id is not None and item.project_id != project_id:
        raise ToolError(f"Item not found in this project: {query}")
    warning = None
    if resolved.get("low_confidence"):
        warning = (
            f"resolved '{query}' → '{resolved['title']}' with low confidence; "
            "pass item_id to confirm."
        )
    return item, warning


def _item_brief(i: Item, scope_map: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": str(i.id), "title": i.title, "type": i.type, "status": i.status,
        "priority": i.priority, "impact_ai": i.impact_ai, "effort_ai": i.effort_ai,
        "scope_id": str(i.scope_id), "origin": i.origen,
        "scope": scope_map.get(str(i.scope_id)) if scope_map is not None else None,
        "thread_id": str(i.thread_id) if i.thread_id is not None else None,
    }


# ---------- Read tools ----------

async def pulsyr_context(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    pid = _pid(token)
    area_name = args.get("area")
    work = args.get("work_description")
    scope = None
    if area_name:
        q = select(Scope).where(
            func.lower(Scope.name) == str(area_name).strip().lower(),
            Scope.project_id == pid,
        )
        scope = (await db.execute(q)).scalar_one_or_none()

    scope_id = scope.id if scope else None

    qw = await service.list_items(
        db, project_id=pid, scope=scope_id, statuses=_OPEN, quickwins=True, order="impact", limit=5
    )
    if not qw:
        base = select(Item).where(Item.status.in_(_OPEN), Item.project_id == pid)
        if scope_id:
            base = base.where(Item.scope_id == scope_id)
        qw = list((await db.execute(
            base.where(Item.priority.in_(["p0", "p1"])).order_by(Item.priority).limit(5)
        )).scalars().all())

    blockers = await service.list_items(
        db, project_id=pid, scope=scope_id, statuses=["blocked"], order="impact", limit=10
    )

    try:
        sentry = (await db.execute(text(
            "SELECT id, title, level FROM sentry_issues "
            "WHERE item_id IS NULL AND project_id = :pid "
            "ORDER BY last_seen DESC NULLS LAST LIMIT 5"
        ), {"pid": pid})).mappings().all()
        sentry_unlinked = [{"id": str(r["id"]), "title": r["title"], "level": r["level"]} for r in sentry]
    except Exception:
        sentry_unlinked = []

    try:
        threads = (await db.execute(text(
            "SELECT id, title FROM threads WHERE stage = 'in-development' AND project_id = :pid LIMIT 5"
        ), {"pid": pid})).mappings().all()
        active_threads = [{"id": str(r["id"]), "title": r["title"]} for r in threads]
    except Exception:
        active_threads = []

    smap = await _scope_map(db, pid)
    result: dict[str, Any] = {
        "local": {
            "quickwins": [_item_brief(i, smap) for i in qw],
            "blockers": [_item_brief(i, smap) for i in blockers],
            "sentry_unlinked": sentry_unlinked,
            "active_threads": active_threads,
        },
        "neighborhood": await graph.neighborhood(db, scope.id) if scope else [],
    }

    if work and await service.touch_embedding_available(db):
        from app.ai import llm
        vec = await llm.embed_text(work)
        if vec:
            rows = (await db.execute(text("""
                SELECT id, title, status FROM items
                WHERE embedding IS NOT NULL AND status NOT IN ('done','discarded')
                  AND project_id = :pid
                ORDER BY embedding <=> CAST(:vec AS vector) LIMIT 5
            """), {"vec": str(vec), "pid": pid})).mappings().all()
            result["semantic"] = [{"id": str(r["id"]), "title": r["title"]} for r in rows]
        else:
            result["semantic"] = None
            result["semantic_status"] = "no-query-embedding"
    else:
        result["semantic"] = None
        result["semantic_status"] = "pending" if work else "not-requested"

    return result


async def pulsyr_search(db: AsyncSession, token: ApiToken, args: dict) -> list[dict]:
    pid = _pid(token)
    q = args["q"]
    limit = int(args.get("limit", 10))
    area_name = (args.get("area") or "").strip() or None
    tipo = (args.get("type") or "").strip() or None

    fetch = limit * 4 if (area_name or tipo) else limit
    rows = await search_items(db, q, limit=max(fetch, limit), with_scope=True)

    # Project filter: only rows whose scope belongs to this project
    if pid:
        scope_ids = set((await _scope_map(db, pid)).keys())
        rows = [r for r in rows if r.get("scope_id") in scope_ids]
    if area_name:
        rows = [r for r in rows if (r.get("scope") or "").lower() == area_name.lower()]
    if tipo:
        rows = [r for r in rows if r.get("type") == tipo]
    rows = rows[:limit]

    return [
        {"id": r["id"], "title": r["title"], "summary_md": r.get("summary_md"),
         "type": r["type"], "status": r["status"], "scope_id": r.get("scope_id"),
         "area": r.get("scope"), "effort_ai": r.get("effort_ai"),
         "impact_ai": r.get("impact_ai")}
        for r in rows
    ]


async def pulsyr_list(db: AsyncSession, token: ApiToken, args: dict) -> list[dict]:
    pid = _pid(token)
    items = await service.list_items(
        db,
        project_id=pid,
        scope=(args.get("area") or None),
        statuses=args.get("status") or None,
        type=(args.get("type") or None),
        order=args.get("order", "impact"),
        quickwins=bool(args.get("quickwins")),
        limit=int(args.get("limit", 20)),
    )
    smap = await _scope_map(db, pid)
    return [_item_brief(i, smap) for i in items]


async def pulsyr_areas(db: AsyncSession, token: ApiToken, args: dict) -> list[dict]:
    pid = _pid(token)
    rows = (await db.execute(text("""
        SELECT s.name, s.description,
               count(i.id) FILTER (WHERE i.status NOT IN ('done','discarded')) AS open_count,
               count(i.id) AS total,
               (array_agg(i.title ORDER BY i.created_at DESC)
                FILTER (WHERE i.status NOT IN ('done','discarded')))[1:3] AS examples
        FROM scopes s LEFT JOIN items i ON i.scope_id = s.id
        WHERE s.project_id = :pid AND s.archived = false
        GROUP BY s.id, s.name, s.description
        ORDER BY open_count DESC, s.name
    """), {"pid": pid})).mappings().all()
    return [
        {"name": r["name"], "description": r["description"],
         "open_items": r["open_count"], "total_items": r["total"],
         "examples": list(r["examples"] or [])}
        for r in rows
    ]


# ---------- Write tools ----------

async def pulsyr_create(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    pid = _pid(token)
    title = (args.get("title") or "").strip()
    if not title:
        raise ToolError("title cannot be empty.")
    area_name = (args.get("area_name") or "").strip()
    if not area_name:
        raise ToolError("area_name cannot be empty.")

    item_type = args.get("type")
    if item_type not in ITEM_TYPES:
        raise ToolError(f"invalid type '{item_type}'; use one of: {', '.join(ITEM_TYPES)}.")

    origin = args.get("origin", "ai-session")
    if origin not in ORIGENES:
        raise ToolError(f"invalid origin '{origin}'; use one of: {', '.join(ORIGENES)}.")

    effort_ai = args.get("effort_ai")
    if effort_ai is not None and effort_ai not in EFFORTS:
        raise ToolError(f"invalid effort_ai '{effort_ai}'; use one of: {', '.join(EFFORTS)} (or null).")

    impact_ai = args.get("impact_ai")
    if impact_ai is not None:
        if not isinstance(impact_ai, int) or isinstance(impact_ai, bool) or not (1 <= impact_ai <= 5):
            raise ToolError("impact_ai must be an integer 1-5 (or null).")

    scope_existed = await _scope_exists(db, area_name, pid)
    scope = await _resolve_scope(db, area_name, create=True, project_id=pid)
    area_created = not scope_existed

    thread_id = None
    ref = args.get("thread_id")
    if ref:
        from app.threads.models import Thread
        thread = await db.get(Thread, _uuid_or_error(ref, "thread_id"))
        if thread is None:
            raise ToolError(f"Thread not found: {ref}")
        if thread.project_id != pid:
            raise ToolError(f"Thread not found in this project: {ref}")
        thread_id = thread.id

    # Idempotency: if an open item with the same title+area exists, return it.
    existing = (await db.execute(
        select(Item).where(
            Item.scope_id == scope.id,
            Item.project_id == pid,
            func.lower(Item.title) == title.lower(),
            Item.status.in_(_OPEN),
        ).limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        return {**_item_brief(existing, {str(scope.id): scope.name}),
                "already_existed": True, "area_created": area_created}

    item = Item(
        scope_id=scope.id, project_id=pid, title=title, type=item_type,
        summary_md=args.get("summary"), status="backlog",
        impact_ai=impact_ai, effort_ai=effort_ai,
        origen=origin, created_by=await actor_for(db, token),
        thread_id=thread_id,
    )
    db.add(item)
    await db.flush()
    return {**_item_brief(item, {str(scope.id): scope.name}),
            "already_existed": False, "area_created": area_created}


async def pulsyr_advance(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    pid = _pid(token)
    if "to_status" not in args:
        raise ToolError("Missing to_status (target status).")
    item, warning = await _resolve_item_verbose(
        db, args.get("item_id"), args.get("query"), project_id=pid
    )
    to = args["to_status"]
    if not valid_transition(item.status, to):
        raise ToolError(f"Invalid transition: {item.status} → {to}")
    try:
        await service.apply_transition(db, item, to, await actor_for(db, token))
    except service.TransitionError as e:
        raise ToolError(str(e)) from e
    out = _item_brief(item, await _scope_map(db, pid))
    if warning:
        out["warning"] = warning
    return out


async def pulsyr_complete(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    pid = _pid(token)
    item, warning = await _resolve_item_verbose(
        db, args.get("item_id"), args.get("search_query"),
        what="item_id or search_query", project_id=pid,
    )
    try:
        unblocked = await service.close_item(
            db, item, "done", args.get("note"), await actor_for(db, token),
            commit_sha=args.get("commit_sha"),
        )
    except service.TransitionError as e:
        raise ToolError(str(e)) from e
    out = {**_item_brief(item, await _scope_map(db, pid)), "unblocked": unblocked}
    if warning:
        out["warning"] = warning
    return out


async def pulsyr_move_area(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    pid = _pid(token)
    if not (args.get("area_name") or "").strip():
        raise ToolError("Missing area_name (destination area).")
    item, warning = await _resolve_item_verbose(
        db, args.get("item_id"), args.get("query"), project_id=pid
    )
    scope = await _resolve_scope(db, args["area_name"], create=False, project_id=pid)
    item.scope_id = scope.id
    await db.flush()
    out = _item_brief(item, {str(scope.id): scope.name})
    if warning:
        out["warning"] = warning
    return out


async def pulsyr_link(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    pid = _pid(token)
    if "relation" not in args:
        raise ToolError("Missing relation (edge type).")
    source, w_src = await _resolve_item_verbose(
        db, args.get("source_id"), args.get("source_query"),
        what="source_id or source_query", project_id=pid,
    )
    target, w_tgt = await _resolve_item_verbose(
        db, args.get("target_id"), args.get("target_query"),
        what="target_id or target_query", project_id=pid,
    )
    try:
        rel = await relationships.create_relationship(
            db, source.id, target.id, args["relation"], args.get("note")
        )
    except relationships.RelationshipError as e:
        raise ToolError(str(e)) from e
    out = {"source_id": str(rel.source_id), "target_id": str(rel.target_id),
           "relation": rel.relation}
    warnings = [w for w in (w_src, w_tgt) if w]
    if warnings:
        out["warning"] = " | ".join(warnings)
    return out


# ---------- Thread tools ----------

def _thread_brief(t: Any) -> dict[str, Any]:
    return {"id": str(t.id), "title": t.title, "stage": t.stage, "scope_id": str(t.scope_id)}


async def pulsyr_thread_create(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    from app.threads import service as tservice
    pid = _pid(token)
    t = await tservice.create_thread(db, args["area_name"], args["title"], args.get("summary"), pid)
    return _thread_brief(t)


async def pulsyr_thread_advance(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    from app.threads import service as tservice
    pid = _pid(token)
    t = await tservice.get_thread(db, _uuid_or_error(args.get("thread_id"), "thread_id"))
    if t is None:
        raise ToolError("Thread not found.")
    if t.project_id != pid:
        raise ToolError("Thread not found in this project.")
    artifact = args.get("artifact")
    content = artifact.get("content") if isinstance(artifact, dict) else None
    try:
        await tservice.advance_stage(db, t, content, await actor_user_id(db, token))
    except tservice.ThreadError as e:
        raise ToolError(str(e)) from e
    return _thread_brief(t)


async def pulsyr_thread_list(db: AsyncSession, token: ApiToken, args: dict) -> list[dict]:
    from app.threads import service as tservice
    pid = _pid(token)
    threads = await tservice.list_threads(db, args.get("stage"), args.get("area"), pid)
    return [_thread_brief(t) for t in threads]


async def pulsyr_thread(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    from app.threads.models import Thread, ThreadArtifact
    pid = _pid(token)
    thread = await db.get(Thread, _uuid_or_error(args.get("id"), "id"))
    if thread is None:
        raise ToolError("Thread not found.")
    if thread.project_id != pid:
        raise ToolError("Thread not found in this project.")
    arts = (await db.execute(
        select(ThreadArtifact).where(ThreadArtifact.thread_id == thread.id)
        .order_by(ThreadArtifact.created_at)
    )).scalars().all()
    items = (await db.execute(
        select(Item).where(Item.thread_id == thread.id).order_by(Item.created_at)
    )).scalars().all()
    smap = await _scope_map(db, pid)
    return {
        **_thread_brief(thread),
        "summary_md": thread.summary_md,
        "artifacts": [{"stage": a.stage, "kind": a.kind, "content_md": a.content_md} for a in arts],
        "items": [_item_brief(i, smap) for i in items],
    }


async def pulsyr_thread_link(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    from app.threads.models import Thread
    pid = _pid(token)
    ref = args.get("thread_id")
    if not ref:
        raise ToolError("Missing thread_id.")
    thread = await db.get(Thread, _uuid_or_error(ref, "thread_id"))
    if thread is None:
        raise ToolError(f"Thread not found: {ref}")
    if thread.project_id != pid:
        raise ToolError(f"Thread not found in this project: {ref}")
    item, warning = await _resolve_item_verbose(
        db, args.get("item_id"), args.get("query"), project_id=pid
    )
    item.thread_id = thread.id
    await db.flush()
    out = {**_item_brief(item, await _scope_map(db, pid)),
           "thread_id": str(thread.id), "thread_title": thread.title}
    if warning:
        out["warning"] = warning
    return out


# ---------- Incident tools ----------

async def pulsyr_incidents(db: AsyncSession, token: ApiToken, args: dict) -> list[dict]:
    from app.webhooks.models import SentryIssue
    pid = _pid(token)
    q = select(SentryIssue).where(SentryIssue.project_id == pid).order_by(
        SentryIssue.last_seen.desc().nulls_last()
    )
    status = args.get("status", "new")
    if status and status != "all":
        q = q.where(SentryIssue.status == status)
    rows = (await db.execute(q.limit(int(args.get("limit", 30))))).scalars().all()
    return [
        {"id": str(i.id), "sentry_issue_id": i.sentry_issue_id, "title": i.title,
         "project": i.project, "level": i.level, "events": i.events_count,
         "triage": i.triage, "status": i.status,
         "first_seen": i.first_seen.isoformat() if i.first_seen else None,
         "last_seen": i.last_seen.isoformat() if i.last_seen else None,
         "web_url": (i.payload or {}).get("web_url") if isinstance(i.payload, dict) else None}
        for i in rows
    ]


async def pulsyr_incident(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    from app.webhooks import service as wservice
    from app.webhooks.models import SentryIssue
    pid = _pid(token)
    issue = await db.get(SentryIssue, _uuid_or_error(args.get("id"), "id"))
    if issue is None or issue.project_id != pid:
        raise ToolError("Incident not found in this project.")
    out = {"id": str(issue.id), "sentry_issue_id": issue.sentry_issue_id, "title": issue.title,
           "project": issue.project, "level": issue.level, "events": issue.events_count,
           "triage": issue.triage, "status": issue.status,
           "first_seen": issue.first_seen.isoformat() if issue.first_seen else None,
           "last_seen": issue.last_seen.isoformat() if issue.last_seen else None,
           "web_url": (issue.payload or {}).get("web_url") if isinstance(issue.payload, dict) else None}
    try:
        from app.webhooks import connection as sconn
        conn = await sconn.outbound(db, issue.account_id)
        detail = await wservice.fetch_issue_detail(
            issue.sentry_issue_id,
            api_token=conn.api_token if conn else None,
            base_url=sconn.effective_base_url(conn) if conn else None,
        )
        out["stacktrace"] = detail.get("stacktrace")
        out["culprit"] = detail.get("culprit")
    except Exception as e:
        logger.warning("pulsyr_incident: failed to fetch stack trace for %s: %s",
                       issue.sentry_issue_id, e)
        out["stacktrace"] = None
        out["detail_error"] = f"Could not fetch stack trace: {str(e)[:160]}"
    return out


async def pulsyr_incident_resolve(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    from app.webhooks import service as wservice
    from app.webhooks.models import SentryIssue
    pid = _pid(token)
    issue = await db.get(SentryIssue, _uuid_or_error(args.get("id"), "id"))
    if issue is None or issue.project_id != pid:
        raise ToolError("Incident not found in this project.")
    return await wservice.resolve_issue(
        db, issue,
        in_sentry=bool(args.get("resolve_in_sentry", True)),
        nota=args.get("note"),
        actor=await actor_for(db, token),
        commit_sha=args.get("commit_sha"),
    )


# ---------- Management tools (documentos / pendientes / gantt) ----------
#
# The PMO tab as a memory bank for Claude: documents (deliverables), pendings, and the
# Gantt plan. The Gantt is edited ONLY here (the UI renders it read-only). All project-scoped.

_INLINE_LIMIT = 256 * 1024  # cap for inlining deliverable content into the agent context


def _date_or_error(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError) as e:
        raise ToolError(f"{field} must be an ISO date (YYYY-MM-DD): '{value}'.") from e


def _deliverable_brief(d: Any) -> dict[str, Any]:
    return {"id": str(d.id), "name": d.name, "compartment_id": str(d.compartment_id),
            "doc_type": d.doc_type, "status": d.status, "owner": d.owner,
            "summary_md": d.summary_md, "current_version": d.current_version}


def _pending_brief(p: Any) -> dict[str, Any]:
    return {"id": str(p.id), "title": p.title, "owner": p.owner, "status": p.status,
            "due_date": p.due_date.isoformat() if p.due_date else None,
            "detail_md": p.detail_md,
            "plan_task_id": str(p.plan_task_id) if p.plan_task_id else None}


def _plan_task_brief(t: Any) -> dict[str, Any]:
    return {"id": str(t.id), "name": t.name,
            "parent_id": str(t.parent_id) if t.parent_id else None,
            "start_date": t.start_date.isoformat() if t.start_date else None,
            "end_date": t.end_date.isoformat() if t.end_date else None,
            "progress": t.progress, "is_milestone": t.is_milestone,
            "deps": t.deps or [], "sort_order": t.sort_order}


# --- documentos ---

async def pulsyr_doc_list(db: AsyncSession, token: ApiToken, args: dict) -> list[dict]:
    pid = _pid(token)
    comp_id = _uuid_or_error(args["compartment_id"], "compartment_id") if args.get("compartment_id") else None
    try:
        rows = await mgmt.list_deliverables(
            db, pid, compartment_id=comp_id,
            status=(args.get("status") or None), q=(args.get("q") or None),
        )
    except mgmt.ManagementError as e:
        raise ToolError(str(e)) from e
    comps = {str(c.id): c.name for c in await mgmt.list_compartments(db, pid)}
    return [{**_deliverable_brief(d), "compartment": comps.get(str(d.compartment_id))} for d in rows]


async def pulsyr_doc_get(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    pid = _pid(token)
    did = _uuid_or_error(args.get("deliverable_id"), "deliverable_id")
    d = await mgmt.get_deliverable(db, pid, did)
    if d is None:
        raise ToolError("Deliverable not found in this project.")
    versions = await mgmt.list_versions(db, d.id)
    out: dict[str, Any] = {
        **_deliverable_brief(d),
        "versions": [{"version_no": v.version_no, "size_bytes": v.size_bytes, "note": v.note,
                      "created_at": v.created_at.isoformat()} for v in versions],
    }
    if args.get("include_content"):
        _, v = await mgmt.get_version(db, pid, d.id)
        if v.size_bytes > _INLINE_LIMIT:
            out["content_note"] = (
                f"content is {v.size_bytes} bytes; too large to inline — download via the Management UI."
            )
        elif d.doc_type in ("md", "html"):
            out["content_text"] = v.content.decode("utf-8", errors="replace")
        else:
            out["content_base64"] = base64.b64encode(v.content).decode("ascii")
    return out


async def pulsyr_doc_put(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    pid = _pid(token)
    b64 = args.get("content_base64")
    raw = args.get("content")
    if b64:
        try:
            content = base64.b64decode(b64, validate=True)
        except Exception as e:
            raise ToolError("content_base64 is not valid base64.") from e
    elif raw is not None:
        content = str(raw).encode("utf-8")
    else:
        raise ToolError("provide content (text) or content_base64 (binary).")
    try:
        d, created = await mgmt.put_deliverable(
            db, pid, compartment_name=args.get("compartment", ""), name=args.get("name", ""),
            doc_type=args.get("doc_type", ""), content=content,
            actor=await actor_for(db, token), summary_md=args.get("summary_md"),
            status=args.get("status"), owner=args.get("owner"), note=args.get("note"),
        )
    except mgmt.ManagementError as e:
        raise ToolError(str(e)) from e
    return {**_deliverable_brief(d), "new_version": created}


# --- pendientes ---

async def pulsyr_pending_list(db: AsyncSession, token: ApiToken, args: dict) -> list[dict]:
    pid = _pid(token)
    ptid = _uuid_or_error(args["plan_task_id"], "plan_task_id") if args.get("plan_task_id") else None
    rows = await mgmt.list_pendings(
        db, pid, status=(args.get("status") or None), owner=(args.get("owner") or None),
        overdue=bool(args.get("overdue")), plan_task_id=ptid,
    )
    return [_pending_brief(p) for p in rows]


async def pulsyr_pending_upsert(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    pid = _pid(token)
    pending_id = _uuid_or_error(args["pending_id"], "pending_id") if args.get("pending_id") else None
    due = _date_or_error(args["due_date"], "due_date") if args.get("due_date") else None
    ptid = _uuid_or_error(args["plan_task_id"], "plan_task_id") if args.get("plan_task_id") else None
    try:
        p = await mgmt.upsert_pending(
            db, pid, actor=await actor_for(db, token), pending_id=pending_id,
            title=args.get("title"), detail_md=args.get("detail_md"), owner=args.get("owner"),
            status=args.get("status"), due_date=due, plan_task_id=ptid,
        )
    except mgmt.ManagementError as e:
        raise ToolError(str(e)) from e
    return _pending_brief(p)


async def pulsyr_pending_complete(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    pid = _pid(token)
    pending_id = _uuid_or_error(args.get("pending_id"), "pending_id")
    try:
        p = await mgmt.complete_pending(db, pid, pending_id, await actor_for(db, token))
    except mgmt.ManagementError as e:
        raise ToolError(str(e)) from e
    return _pending_brief(p)


# --- gantt (plan) ---

async def pulsyr_gantt_get(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    from app.management import gantt as _gantt
    pid = _pid(token)
    tasks = await mgmt.list_plan_tasks(db, pid)
    start, end = _gantt.plan_bounds(mgmt.plan_tasks_to_dicts(tasks))
    return {
        "tasks": [_plan_task_brief(t) for t in tasks],
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
    }


async def pulsyr_gantt_task_upsert(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    pid = _pid(token)
    task_id = _uuid_or_error(args["task_id"], "task_id") if args.get("task_id") else None
    parent_id = _uuid_or_error(args["parent_id"], "parent_id") if args.get("parent_id") else None
    start = _date_or_error(args["start_date"], "start_date") if args.get("start_date") else None
    end = _date_or_error(args["end_date"], "end_date") if args.get("end_date") else None
    deps = args.get("deps")
    if deps is not None and not isinstance(deps, list):
        raise ToolError("deps must be a list of plan_task ids.")
    try:
        t = await mgmt.upsert_plan_task(
            db, pid, actor=await actor_for(db, token), task_id=task_id, name=args.get("name"),
            parent_id=parent_id, start_date=start, end_date=end, progress=args.get("progress"),
            is_milestone=args.get("is_milestone"), deps=deps, sort_order=args.get("sort_order"),
        )
    except mgmt.ManagementError as e:
        raise ToolError(str(e)) from e
    return _plan_task_brief(t)


async def pulsyr_gantt_task_remove(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    pid = _pid(token)
    task_id = _uuid_or_error(args.get("task_id"), "task_id")
    try:
        await mgmt.remove_plan_task(db, pid, task_id, await actor_for(db, token))
    except mgmt.ManagementError as e:
        raise ToolError(str(e)) from e
    return {"removed": str(task_id)}
