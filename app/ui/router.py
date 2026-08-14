import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BeforeValidator
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.deps import current_user_ui
from app.auth.models import ApiToken, User
from app.database import get_db
from app.i18n import resolve_lang
from app.i18n import t as _t
from app.items import graph, service
from app.items.lifecycle import allowed_targets, non_terminal_targets
from app.items.models import Item, ItemComment, ItemEvent
from app.jobs.models import AgentRun
from app.projects.access import resolve_current_project, user_role_on_project
from app.scopes.models import Scope
from app.templates_config import templates
from app.ui.flash import flash_success

router = APIRouter(tags=["ui"])

_OPEN = ["idea", "backlog", "spec", "in-progress", "blocked", "in-review"]
# ponytail: fetch cap keeps the page bounded; the counter shows "{n}+" when hit.
_BACKLOG_FETCH_CAP = 300

# The #filters form serializes "" for booleans that are off (HTML contract: the hidden
# inputs and the chips use the empty string = off). These types accept that contract:
# "" → None/False instead of Pydantic's 422 (bug 2026-07-10, BOARD/CLOSED buttons).
OptBool = Annotated[bool | None, BeforeValidator(lambda v: None if v == "" else v)]
FlagBool = Annotated[bool, BeforeValidator(lambda v: False if v == "" else v)]


@router.get("/ui/lang/{code}")
async def ui_set_lang(code: str, request: Request, next: str = "/"):
    """Language switch — no auth dependency so it also works on the login page."""
    from app.i18n import SUPPORTED

    if code not in SUPPORTED:
        return Response(status_code=404)
    request.session["lang"] = code
    # Open-redirect guard: only same-site relative paths.
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    return RedirectResponse(target, status_code=303)
_PRIORITY_RANK = {"p0": 0, "p1": 1, "p2": 2, "p3": 3, None: 9}


def _recent_touch(item: Item) -> bool:
    if item.last_touched_at is None:
        return False
    return item.last_touched_at > datetime.now(timezone.utc) - timedelta(hours=24)


async def _project_id(db: AsyncSession, user: User, request: Request) -> uuid.UUID:
    """Current project for UI list screens; a zero-UUID sentinel (matches nothing) when the
    user can reach no project — so they see an empty board, never another account's data."""
    p = await resolve_current_project(db, user, request)
    return p.id if p else uuid.UUID(int=0)


async def _guard_row(
    db: AsyncSession, user: User, project_id, *, write: bool = False
) -> Response | None:
    """404 if the row's project isn't accessible to the user; 403 if a viewer attempts a write."""
    role = await user_role_on_project(db, user, project_id)
    if role is None:
        return Response(status_code=404)
    if write and role == "viewer":
        return Response(content="Viewer cannot modify this project", status_code=403)
    return None


# ---------- Dashboard ----------

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.models import Thread
    from app.webhooks.models import SentryIssue

    pid = await _project_id(db, user, request)
    counts_q = await db.execute(
        select(Item.status, func.count().label("n"))
        .where(Item.project_id == pid).group_by(Item.status)
    )
    counts = {row.status: row.n for row in counts_q}
    blocked_ids = await graph.graph_blocked_ids(db, project_id=pid)

    quick_wins_n = int(await db.scalar(
        select(func.count()).select_from(Item).where(
            Item.project_id == pid, Item.impact_ai >= 4,
            Item.effort_ai.in_(["XS", "S"]), Item.status.not_in(["done", "discarded"]),
        )
    ) or 0)
    threads_active = int(await db.scalar(
        select(func.count()).select_from(Thread).where(
            Thread.project_id == pid, Thread.stage.not_in(["done", "discarded"]),
        )
    ) or 0)
    incidents_new = int(await db.scalar(
        select(func.count()).select_from(SentryIssue).where(
            SentryIssue.project_id == pid, SentryIssue.status == "new",
        )
    ) or 0)

    recent_q = await db.execute(
        select(Item).where(Item.project_id == pid).order_by(Item.created_at.desc()).limit(10)
    )
    recent = recent_q.scalars().all()
    cost_q = await db.scalar(
        select(func.sum(AgentRun.cost_usd)).where(AgentRun.status == "ok", AgentRun.project_id == pid)
    )
    scopes = list((await db.execute(
        select(Scope).where(Scope.archived.is_(False), Scope.project_id == pid).order_by(Scope.name)
    )).scalars().all())

    from datetime import datetime as _dt_cls
    week_start = _dt_cls.now(timezone.utc)
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = week_start - timedelta(days=week_start.weekday())
    closed_this_week = int(await db.scalar(
        select(func.count()).select_from(Item).where(
            Item.project_id == pid,
            Item.status.in_(["done", "discarded"]),
            Item.closed_at >= week_start,
        )
    ) or 0)

    cards = {
        "open": sum(counts.get(s, 0) for s in ("backlog", "spec", "in-progress", "blocked", "in-review")),
        "in_progress": counts.get("in-progress", 0),
        "blocked": len(blocked_ids),
        "quick_wins": quick_wins_n,
        "threads_active": threads_active,
        "incidents_new": incidents_new,
        "closed_this_week": closed_this_week,
    }
    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "user": user, "cards": cards, "recent": recent,
            "recent_touch": {str(i.id): _recent_touch(i) for i in recent},
            "monthly_cost": float(cost_q or 0), "scopes": scopes,
        },
    )


# ---------- Backlog ----------

_BOARD_STATUSES = ["idea", "backlog", "spec", "in-progress", "in-review", "blocked"]


async def _backlog_context(
    request: Request,
    db: AsyncSession,
    user: User,
    *,
    scope: str | None = None,
    status: str | None = None,
    item_type: str | None = None,
    origen: str | None = None,
    stale: bool | None = None,
    graph_blocked: bool | None = None,
    order: str = "priority",
    show: str = "open",
    q: str | None = None,
    priority: str | None = None,
    effort: str | None = None,
    quickwins: bool = False,
    urgent: bool = False,
    agent_ready: bool = False,
    view: str = "list",
    group: str = "",
) -> dict:
    """Build the backlog context (filtered items + derived state + board/group).

    Shared by GET /backlog and POST /ui/items/{id}/board-move, so that a drag&drop
    move re-renders the board with the SAME active filters and recomputes
    blocks/ready/counters (moving one card can unblock others).
    """
    from sqlalchemy import case as sa_case

    from app.items.search import search_items as _fts

    pid = await _project_id(db, user, request)
    q_base = select(Item).where(Item.project_id == pid)

    # The board never shows terminal states: a terminal status filter is ignored (spec §1.5).
    if view == "board" and status in ("done", "discarded"):
        status = None

    # status / show filter
    if status:
        q_base = q_base.where(Item.status == status)
    elif view == "board":
        q_base = q_base.where(Item.status.in_(_BOARD_STATUSES))
    elif show == "open":
        q_base = q_base.where(Item.status.in_(_OPEN))
    elif show == "closed":
        q_base = q_base.where(Item.status.in_(["done", "discarded"]))
    # show == "all": no filter

    if scope:
        scope_row = await db.scalar(
            select(Scope).where(Scope.name == scope, Scope.project_id == pid)
        )
        if scope_row:
            q_base = q_base.where(Item.scope_id == scope_row.id)
        else:
            q_base = q_base.where(Item.id == uuid.UUID(int=0))

    if item_type:
        q_base = q_base.where(Item.type == item_type)
    if origen:
        q_base = q_base.where(Item.origen == origen)
    if stale is not None:
        q_base = q_base.where(Item.stale_risk == stale)
    if priority:
        q_base = q_base.where(Item.priority == priority)
    if effort:
        q_base = q_base.where(Item.effort_ai == effort)
    if urgent:
        q_base = q_base.where(Item.priority.in_(["p0", "p1"]))
    if quickwins:
        q_base = q_base.where(Item.impact_ai >= 4, Item.effort_ai.in_(["XS", "S"]))
    if agent_ready:
        q_base = q_base.where(Item.agent_ready.is_(True))

    # FTS: filter in SQL (before the LIMIT) so matches outside the top-300 are not lost.
    fts_rank: dict[str, int] | None = None
    if q:
        fts_rows = await _fts(db, q, project_id=pid, limit=300)
        if fts_rows:
            q_base = q_base.where(Item.id.in_([uuid.UUID(r["id"]) for r in fts_rows]))
            fts_rank = {r["id"]: n for n, r in enumerate(fts_rows)}
        else:
            q_base = q_base.where(Item.id == uuid.UUID(int=0))

    # SQL ordering — topological stays in Python (Spanish aliases: legacy URLs)
    if order in ("priority", "prioridad"):
        q_base = q_base.order_by(
            sa_case(
                (Item.priority == "p0", 0),
                (Item.priority == "p1", 1),
                (Item.priority == "p2", 2),
                (Item.priority == "p3", 3),
                else_=9,
            ),
            Item.impact_ai.desc().nullslast(),
        )
    elif order in ("impact", "impacto"):
        q_base = q_base.order_by(Item.impact_ai.desc().nullslast())
    elif order in ("recent", "reciente"):
        q_base = q_base.order_by(Item.created_at.desc())

    q_base = q_base.limit(_BACKLOG_FETCH_CAP)
    items = list((await db.execute(q_base)).scalars().all())
    capped = len(items) >= _BACKLOG_FETCH_CAP

    # With an active search and the default ordering, the order is relevance (FTS rank).
    if fts_rank is not None and order in ("priority", "prioridad"):
        items.sort(key=lambda i: fts_rank.get(str(i.id), 1_000_000))

    blocked_ids = await graph.graph_blocked_ids(db, project_id=pid)
    unblocker_ids = await graph.unblocker_ids(db, project_id=pid)

    if graph_blocked:
        items = [i for i in items if str(i.id) in blocked_ids]

    # Topological ordering (Python; needs graph)
    if order in ("topological", "topologico"):
        topo = await _topo_order_ids(db, items)
        items = _order_items(items, "topological", topo)

    # ready_ids: agent_ready + open state + not graph-blocked
    ready_ids = {
        str(i.id) for i in items
        if i.agent_ready and i.status in ("backlog", "spec") and str(i.id) not in blocked_ids
    }

    scopes = list((await db.execute(
        select(Scope).where(Scope.archived.is_(False), Scope.project_id == pid).order_by(Scope.name)
    )).scalars().all())
    scope_map = {s.id: s.name for s in scopes}

    # can_write: gates board drag&drop (viewers can look but not move).
    role = await user_role_on_project(db, user, pid)
    can_write = role is not None and role != "viewer"

    ctx: dict = {
        "user": user,
        "items": items,
        "scopes": scopes,
        "scope_map": scope_map,
        "blocked_ids": blocked_ids,
        "unblocker_ids": unblocker_ids,
        "ready_ids": ready_ids,
        "can_write": can_write,
        "capped": capped,
        "recent_touch": {str(i.id): _recent_touch(i) for i in items},
        "filters": {
            "scope": scope, "status": status, "type": item_type, "origen": origen,
            "stale": stale, "graph_blocked": graph_blocked, "order": order,
            "show": show, "q": q, "priority": priority, "effort": effort,
            "quickwins": quickwins, "urgent": urgent, "agent_ready": agent_ready,
            "view": view, "group": group,
        },
    }

    # Board context
    if view == "board":
        by_status: dict[str, list] = {s: [] for s in _BOARD_STATUSES}
        for item in items:
            if item.status in by_status:
                by_status[item.status].append(item)
        ctx["by_status"] = by_status
        ctx["board_statuses"] = _BOARD_STATUSES

    # Group-by context — localized labels (items_grouped.html renders the headers as-is)
    if group and group != "none" and view != "board":
        lang = resolve_lang(request)
        grouped: dict[str, list] = {}
        for item in items:
            if group == "scope":
                key = scope_map.get(item.scope_id) or _t("backlog.group_no_scope", lang)
            elif group == "type":
                key = _t(f"type.{item.type}", lang) if item.type else _t("backlog.group_no_scope", lang)
            elif group == "priority":
                key = item.priority or _t("backlog.group_no_priority", lang)
            elif group == "status":
                key = item.status
            else:
                key = "—"
            grouped.setdefault(key, []).append(item)
        if group == "status":
            # Funnel order, not alphabetical; label localized while building the list
            _sidx = {s: n for n, s in enumerate([*_BOARD_STATUSES, "done", "discarded"])}
            ctx["groups"] = [
                (_t(f"status.{k}", lang), v)
                for k, v in sorted(grouped.items(), key=lambda kv: _sidx.get(kv[0], 99))
            ]
        else:
            ctx["groups"] = sorted(grouped.items())

    return ctx


@router.get("/backlog", response_class=HTMLResponse)
async def backlog(
    request: Request,
    scope: str | None = None,
    status: str | None = None,
    item_type: str | None = None,
    origen: str | None = None,
    stale: OptBool = None,
    graph_blocked: OptBool = None,
    order: str = "priority",
    show: str = "open",         # "open" | "all" | "closed"
    q: str | None = None,       # FTS search
    priority: str | None = None,
    effort: str | None = None,
    quickwins: FlagBool = False,
    urgent: FlagBool = False,
    agent_ready: FlagBool = False,
    view: str = "list",         # "list" | "board"
    group: str = "",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    ctx = await _backlog_context(
        request, db, user, scope=scope, status=status, item_type=item_type,
        origen=origen, stale=stale, graph_blocked=graph_blocked, order=order,
        show=show, q=q, priority=priority, effort=effort, quickwins=quickwins,
        urgent=urgent, agent_ready=agent_ready, view=view, group=group,
    )
    if request.headers.get("HX-Request"):
        # Controls + items travel together: the hidden carriers and the chip styling
        # must reflect the new state (bug 2026-07-10, form desync).
        return templates.TemplateResponse(request, "partials/backlog_root.html", ctx)
    return templates.TemplateResponse(request, "backlog.html", ctx)


def _order_items(items: list[Item], order: str, topo_rank: dict[str, int] | None) -> list[Item]:
    if order in ("impact", "impacto"):
        return sorted(items, key=lambda i: (-(i.impact_ai or 0), i.effort_ai or "ZZ"))
    if order in ("priority", "prioridad"):
        return sorted(items, key=lambda i: (_PRIORITY_RANK.get(i.priority, 9), -(i.impact_ai or 0)))
    if order in ("topological", "topologico") and topo_rank is not None:
        return sorted(items, key=lambda i: topo_rank.get(str(i.id), 1_000_000))
    return sorted(items, key=lambda i: i.created_at, reverse=True)


async def _topo_order_ids(db: AsyncSession, items: list[Item]) -> dict[str, int]:
    ids = [str(i.id) for i in items]
    if not ids:
        return {}
    rels = await db.execute(
        text("""
            SELECT source_id, target_id, relation FROM item_relationships
            WHERE source_id = ANY(:ids) AND target_id = ANY(:ids)
        """),
        {"ids": ids},
    )
    edges = [(str(r["source_id"]), str(r["target_id"]), r["relation"]) for r in rels.mappings().all()]
    impact = {str(i.id): (i.impact_ai or 0) for i in items}
    result = graph.topological_order(ids, edges, impact)
    return {item_id: rank for rank, item_id in enumerate(result["order"])}


# ---------- Item detail ----------

@router.get("/items/{item_id}", response_class=HTMLResponse)
async def item_detail(
    item_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    result = await db.execute(
        select(Item).where(Item.id == item_id).options(
            selectinload(Item.comments), selectinload(Item.events), selectinload(Item.enrichments),
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        return Response(status_code=404, content="Item not found")
    guard = await _guard_row(db, user, item.project_id)
    if guard is not None:
        return guard

    scope = await db.scalar(select(Scope).where(Scope.id == item.scope_id))
    blockers = await graph.blockers_of(db, item.id)
    sub = await graph.subgraph(db, item.id)
    scopes = list((await db.execute(
        select(Scope).where(Scope.archived.is_(False), Scope.project_id == item.project_id)
        .order_by(Scope.name)
    )).scalars().all())

    return templates.TemplateResponse(
        request,
        "item_detail.html",
        {
            "user": user, "item": item, "scope": scope, "scopes": scopes,
            "transitions": non_terminal_targets(item.status),
            "all_targets": allowed_targets(item.status),
            "blockers": blockers,
            "subgraph": sub,
            "open_titles": await _open_item_titles(db, item.project_id),
        },
    )


# ---------- Priority (impact × effort matrix) ----------

@router.get("/priority", response_class=HTMLResponse)
async def prioridad_page(
    request: Request,
    scope: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    pid = await _project_id(db, user, request)
    q = select(Item).where(Item.status.in_(_OPEN), Item.project_id == pid)
    if scope:
        scope_row = await db.scalar(select(Scope).where(Scope.name == scope, Scope.project_id == pid))
        if scope_row:
            q = q.where(Item.scope_id == scope_row.id)
    items = list((await db.execute(q.limit(500))).scalars().all())

    # Matrix cells: impact (5..1) × effort (XS..XL).
    efforts = ["XS", "S", "M", "L", "XL"]
    matrix: dict[tuple[int, str], list[Item]] = {}
    unestimated: list[Item] = []
    for it in items:
        if it.impact_ai and it.effort_ai:
            matrix.setdefault((it.impact_ai, it.effort_ai), []).append(it)
        else:
            unestimated.append(it)

    ranked = sorted(items, key=lambda i: (_PRIORITY_RANK.get(i.priority, 9), -(i.impact_ai or 0)))
    scopes = list((await db.execute(
        select(Scope).where(Scope.archived.is_(False), Scope.project_id == pid).order_by(Scope.name)
    )).scalars().all())

    ctx = {
        "user": user, "items": ranked, "matrix": matrix, "efforts": efforts,
        "impacts": [5, 4, 3, 2, 1], "unestimated": unestimated,
        "scopes": scopes, "filters": {"scope": scope},
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/priority_body.html", ctx)
    return templates.TemplateResponse(request, "priority.html", ctx)


# ---------- UI actions (HTMX, form-encoded) ----------

def _refresh() -> Response:
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@router.post("/ui/items/{item_id}/comments")
async def ui_add_comment(
    item_id: uuid.UUID,
    request: Request,
    body_md: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    # The detail-page form posts form-urlencoded; the REST endpoint only takes JSON.
    pid = await _project_id(db, user, request)
    item = await db.get(Item, item_id)
    if item is None or item.project_id != pid:
        raise HTTPException(status_code=404, detail="Item not found")
    body = body_md.strip()
    if not body:
        raise HTTPException(status_code=422, detail="Comment cannot be empty")
    db.add(ItemComment(item_id=item_id, author=user.email, body_md=body, kind="comment"))
    await db.commit()
    return _refresh()


@router.post("/ui/items/{item_id}/transition")
async def ui_transition(
    item_id: uuid.UUID,
    status: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    item = await service.get_item(db, item_id)
    if item is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, item.project_id, write=True)
    if guard is not None:
        return guard
    try:
        await service.apply_transition(db, item, status, user.email)
        await db.commit()
    except service.TransitionError as e:
        return Response(content=str(e), status_code=422)
    return _refresh()


@router.post("/ui/items/{item_id}/board-move", response_class=HTMLResponse)
async def ui_board_move(
    item_id: uuid.UUID,
    request: Request,
    status: str = Form(...),
    # Filter passthrough so the board re-renders exactly as it was.
    scope: str | None = Form(None),
    item_type: str | None = Form(None),
    origen: str | None = Form(None),
    # Form() INSIDE the Annotated: outside it, FastAPI drops the BeforeValidator and "" → 422.
    stale: Annotated[OptBool, Form()] = None,
    graph_blocked: Annotated[OptBool, Form()] = None,
    order: str = Form("priority"),
    q: str | None = Form(None),
    priority: str | None = Form(None),
    effort: str | None = Form(None),
    quickwins: FlagBool = Form(False),
    urgent: FlagBool = Form(False),
    agent_ready: FlagBool = Form(False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    """Board drag&drop: apply the transition and return the re-rendered board.

    Invalid move (lifecycle matrix) → returns the board UNCHANGED (the card snaps
    back to its column) + an error toast via HX-Trigger. Never a 422: the partial
    swap always repaints a coherent board.
    """
    import json as _json

    item = await service.get_item(db, item_id)
    if item is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, item.project_id, write=True)
    if guard is not None:
        return guard

    invalid: str | None = None
    try:
        await service.apply_transition(db, item, status, user.email)
        await db.commit()
    except service.TransitionError as e:
        # apply_transition validates BEFORE mutating, so the session is still clean
        # (nothing to revert); a rollback here would expire the objects needed for the re-render.
        invalid = str(e)

    ctx = await _backlog_context(
        request, db, user, scope=scope, item_type=item_type, origen=origen,
        stale=stale, graph_blocked=graph_blocked, order=order, q=q,
        priority=priority, effort=effort, quickwins=quickwins, urgent=urgent,
        agent_ready=agent_ready, view="board", show="open", group="",
    )
    resp = templates.TemplateResponse(request, "partials/items_board.html", ctx)
    if invalid:
        resp.headers["HX-Trigger"] = _json.dumps(
            {"pulsyr:toast": {"message": invalid, "kind": "error"}}
        )
    return resp


@router.post("/ui/items/{item_id}/close")
async def ui_close(
    item_id: uuid.UUID,
    request: Request,
    status: str = Form(...),
    reason: str = Form(""),
    commit_sha: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    item = await service.get_item(db, item_id)
    if item is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, item.project_id, write=True)
    if guard is not None:
        return guard
    try:
        await service.close_item(db, item, status, reason or None, user.email, commit_sha or None)
        await db.commit()
    except service.TransitionError as e:
        return Response(content=str(e), status_code=422)
    if status == "done":
        flash_success(request, title=item.title, celebrate=True)
    else:
        flash_success(request, message=_t("flash.item_discarded", resolve_lang(request)))
    return _refresh()


@router.post("/ui/items/{item_id}/reopen")
async def ui_reopen(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    item = await service.get_item(db, item_id)
    if item is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, item.project_id, write=True)
    if guard is not None:
        return guard
    try:
        await service.reopen_item(db, item, user.email)
        await db.commit()
    except service.TransitionError as e:
        return Response(content=str(e), status_code=422)
    return _refresh()


@router.get("/ui/items/{item_id}/close-modal", response_class=HTMLResponse)
async def ui_close_modal(
    item_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    item = await service.get_item(db, item_id)
    if item is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, item.project_id)
    if guard is not None:
        return guard
    return templates.TemplateResponse(request, "partials/_close_modal.html", {"item": item})


@router.post("/ui/items/{item_id}/field")
async def ui_set_field(
    item_id: uuid.UUID,
    field: str = Form(...),
    value: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    item = await service.get_item(db, item_id)
    if item is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, item.project_id, write=True)
    if guard is not None:
        return guard
    if field == "priority":
        await service.set_priority(db, item, value or None, user.email)
    elif field == "impact_ai":
        item.impact_ai = int(value) if value else None
    elif field == "effort_ai":
        item.effort_ai = value or None
    elif field == "title":
        new_title = value.strip()
        if not new_title:
            return Response(content="Title cannot be empty", status_code=422)
        item.title = new_title
        db.add(ItemEvent(item_id=item.id, actor=user.email, action="edited", payload={"field": "title"}))
    elif field == "summary_md":
        item.summary_md = value.strip() or None
        db.add(ItemEvent(item_id=item.id, actor=user.email, action="edited",
                         payload={"field": "summary_md"}))
    else:
        return Response(content="Field not editable", status_code=422)
    await db.commit()
    return Response(status_code=204, headers={"HX-Refresh": "true"})


async def _open_item_titles(db: AsyncSession, project_id: uuid.UUID | None) -> list[str]:
    """Datalist source for the relationship form — open items of the project."""
    rows = await db.execute(
        select(Item.title).where(Item.project_id == project_id, Item.status.in_(_OPEN))
        .order_by(Item.created_at.desc()).limit(200)
    )
    return list(rows.scalars().all())


async def _render_relations(request: Request, db: AsyncSession, item: Item) -> Response:
    sub = await graph.subgraph(db, item.id)
    return templates.TemplateResponse(
        request, "partials/relationship_list.html",
        {"item": item, "subgraph": sub, "open_titles": await _open_item_titles(db, item.project_id)},
    )


@router.post("/ui/items/{item_id}/relationships")
async def ui_create_relationship(
    item_id: uuid.UUID,
    request: Request,
    relation: str = Form(...),
    target_query: str = Form(...),
    note: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.items import relationships

    item = await service.get_item(db, item_id)
    if item is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, item.project_id, write=True)
    if guard is not None:
        return guard
    try:
        target_id = await relationships.resolve_query(db, target_query)
        await relationships.create_relationship(db, item_id, target_id, relation, note or None)
        await db.commit()
    except relationships.RelationshipError as e:
        return Response(content=str(e), status_code=422)
    return await _render_relations(request, db, item)


@router.delete("/ui/items/{item_id}/relationships")
async def ui_delete_relationship(
    item_id: uuid.UUID,
    request: Request,
    source: uuid.UUID,
    target: uuid.UUID,
    relation: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.items import relationships

    item = await service.get_item(db, item_id)
    if item is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, item.project_id, write=True)
    if guard is not None:
        return guard
    await relationships.delete_relationship(db, source, target, relation)
    await db.commit()
    return await _render_relations(request, db, item)


@router.post("/ui/items/create")
async def ui_create_item(
    request: Request,
    title: str = Form(...),
    scope_id: str = Form(...),
    type: str = Form(...),
    status: str = Form("backlog"),
    summary_md: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    pid = await _project_id(db, user, request)
    scope = await db.get(Scope, uuid.UUID(scope_id))
    if scope is None or scope.project_id != pid:
        return Response(content="Scope does not belong to your project", status_code=422)
    item = Item(
        scope_id=scope.id, project_id=pid, title=title, type=type, status=status,
        summary_md=summary_md or None, origen="human", created_by=user.email,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    flash_success(request, message=_t("flash.item_created", resolve_lang(request)))
    return RedirectResponse(f"/items/{item.id}", status_code=303)


# ---------- Threads ----------

@router.get("/threads", response_class=HTMLResponse)
async def hilos_page(
    request: Request,
    scope: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.models import THREAD_STAGES
    from app.threads.service import list_threads

    pid = await _project_id(db, user, request)
    threads = await list_threads(db, scope_name=scope, project_id=pid, limit=500)
    by_stage: dict[str, list] = {s: [] for s in THREAD_STAGES}
    for t in threads:
        by_stage.setdefault(t.stage, []).append(t)
    # count artifacts and items per thread (two grouped queries, no N+1)
    art_rows = (await db.execute(text(
        "SELECT ta.thread_id, count(*) AS n FROM thread_artifacts ta "
        "JOIN threads t ON t.id = ta.thread_id "
        "WHERE t.project_id = :pid GROUP BY ta.thread_id"
    ), {"pid": pid})).mappings().all()
    item_rows = (await db.execute(text(
        "SELECT thread_id, count(*) AS n FROM items "
        "WHERE project_id = :pid AND thread_id IS NOT NULL GROUP BY thread_id"
    ), {"pid": pid})).mappings().all()
    art_map = {str(r["thread_id"]): r["n"] for r in art_rows}
    item_map = {str(r["thread_id"]): r["n"] for r in item_rows}
    counts = {
        str(t.id): {"artifacts": art_map.get(str(t.id), 0), "items": item_map.get(str(t.id), 0)}
        for t in threads
    }
    scopes = list((await db.execute(
        select(Scope).where(Scope.archived.is_(False), Scope.project_id == pid).order_by(Scope.name)
    )).scalars().all())
    stages = [s for s in THREAD_STAGES if s != "discarded"]
    return templates.TemplateResponse(
        request, "threads.html",
        {"user": user, "by_stage": by_stage, "stages": stages, "counts": counts,
         "scopes": scopes, "filters": {"scope": scope}},
    )


@router.get("/threads/{thread_id}", response_class=HTMLResponse)
async def hilo_detail(
    thread_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.models import ThreadArtifact, next_stage, prev_stage
    from app.threads.service import get_thread

    thread = await get_thread(db, thread_id)
    if thread is None:
        return Response(status_code=404, content="Thread not found")
    guard = await _guard_row(db, user, thread.project_id)
    if guard is not None:
        return guard
    arts = list((await db.execute(
        select(ThreadArtifact).where(ThreadArtifact.thread_id == thread_id)
        .order_by(ThreadArtifact.created_at)
    )).scalars().all())
    linked = list((await db.execute(
        select(Item).where(Item.thread_id == thread_id).order_by(Item.created_at)
    )).scalars().all())
    scope = await db.scalar(select(Scope).where(Scope.id == thread.scope_id))
    return templates.TemplateResponse(
        request, "thread_detail.html",
        {"user": user, "thread": thread, "artifacts": arts, "linked": linked, "scope": scope,
         "next_stage": next_stage(thread.stage), "prev_stage": prev_stage(thread.stage)},
    )


@router.post("/ui/threads/create")
async def ui_create_hilo(
    request: Request,
    title: str = Form(...),
    scope_name: str = Form(...),
    summary: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.service import create_thread

    pid = await _project_id(db, user, request)
    t = await create_thread(db, scope_name, title, summary or None, project_id=pid)
    await db.commit()
    flash_success(request, message=_t("flash.thread_created", resolve_lang(request)))
    return RedirectResponse(f"/threads/{t.id}", status_code=303)


@router.post("/ui/threads/{thread_id}/advance")
async def ui_advance_hilo(
    thread_id: uuid.UUID,
    request: Request,
    artifact_content: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.service import ThreadError, advance_stage, get_thread

    t = await get_thread(db, thread_id)
    if t is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, t.project_id, write=True)
    if guard is not None:
        return guard
    try:
        await advance_stage(db, t, artifact_content or None, user.id)
        await db.commit()
    except ThreadError as e:
        return Response(content=str(e), status_code=422)
    if t.stage == "done":
        flash_success(request, title=t.title, celebrate=True)
    return _refresh()


@router.post("/ui/threads/{thread_id}/stage")
async def ui_set_hilo_stage(
    thread_id: uuid.UUID,
    request: Request,
    stage: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.service import ThreadError, get_thread, set_stage

    t = await get_thread(db, thread_id)
    if t is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, t.project_id, write=True)
    if guard is not None:
        return guard
    try:
        await set_stage(db, t, stage)
        await db.commit()
    except ThreadError as e:
        return Response(content=str(e), status_code=422)
    if t.stage == "done":
        flash_success(request, title=t.title, celebrate=True)
    return _refresh()


@router.post("/ui/threads/{thread_id}/elaborate", response_class=HTMLResponse)
async def ui_elaborate_hilo(
    thread_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.service import ThreadError, elaborate_next_stage, get_thread

    t = await get_thread(db, thread_id)
    if t is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, t.project_id, write=True)
    if guard is not None:
        return guard
    try:
        draft = await elaborate_next_stage(db, t)
    except ThreadError as e:
        return HTMLResponse(
            f'<div class="text-sm text-error">{e}</div>', status_code=200
        )
    return templates.TemplateResponse(
        request, "partials/elaborate_draft.html", {"thread": t, "draft": draft}
    )


# ---------- Incidents (Sentry error container) ----------

@router.get("/incidents", response_class=HTMLResponse)
async def incidentes_page(
    request: Request,
    include_ignored: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.webhooks.models import SentryIssue

    pid = await _project_id(db, user, request)
    q = (
        select(SentryIssue)
        .where(SentryIssue.project_id == pid)
        .order_by(SentryIssue.last_seen.desc().nulls_last())
    )
    if not include_ignored:
        q = q.where(SentryIssue.status != "ignored")
    issues = list((await db.execute(q.limit(200))).scalars().all())

    async def _count(st: str) -> int:
        return int(await db.scalar(
            select(func.count()).select_from(SentryIssue).where(
                SentryIssue.project_id == pid, SentryIssue.status == st
            )
        ) or 0)

    counts = {
        "new": await _count("new"),
        "linked": await _count("linked"),
        "ignored": await _count("ignored"),
    }
    return templates.TemplateResponse(
        request, "incidents.html",
        {"user": user, "issues": issues, "counts": counts, "include_ignored": include_ignored},
    )


@router.post("/ui/incidents/{issue_id}/promote")
async def ui_promote_issue(
    issue_id: uuid.UUID,
    request: Request,
    priority: str = Form("p1"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.webhooks import service as wservice
    from app.webhooks.models import SentryIssue

    issue = await db.get(SentryIssue, issue_id)
    if issue is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, issue.project_id, write=True)
    if guard is not None:
        return guard
    if priority not in ("p0", "p1", "p2", "p3"):
        priority = "p1"
    await wservice.promote_issue(db, issue, priority=priority, actor=user.email)
    await db.commit()
    flash_success(request, message=_t("flash.incident_promoted", resolve_lang(request)))
    return _refresh()


@router.post("/ui/incidents/{issue_id}/ignore")
async def ui_ignore_issue(
    issue_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.webhooks.models import SentryIssue

    issue = await db.get(SentryIssue, issue_id)
    if issue is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, issue.project_id, write=True)
    if guard is not None:
        return guard
    issue.status = "ignored"
    await db.commit()
    flash_success(request, message=_t("flash.incident_ignored", resolve_lang(request)))
    return _refresh()


@router.post("/ui/incidents/{issue_id}/unignore")
async def ui_unignore_issue(
    issue_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.webhooks.models import SentryIssue

    issue = await db.get(SentryIssue, issue_id)
    if issue is None or issue.status != "ignored":
        return Response(status_code=404)
    guard = await _guard_row(db, user, issue.project_id, write=True)
    if guard is not None:
        return guard
    issue.status = "new"
    await db.commit()
    flash_success(request, message=_t("flash.incident_restored", resolve_lang(request)))
    return _refresh()


@router.post("/ui/incidents/backfill")
async def ui_backfill_sentry(
    request: Request,
    query: str = Form("is:unresolved"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    """Import the history from the Sentry API using the account's connection
    + the current project's slug (owner only). Spec 2026-07-10 §4.5."""
    if user.account_role != "owner":
        msg = _t("admin.unauthorized", resolve_lang(request))
        return HTMLResponse(f'<div class="text-sm text-error">{msg}</div>', status_code=403)
    from app.projects.models import Project
    from app.webhooks import connection as sconn
    from app.webhooks import service as wservice

    lang = resolve_lang(request)
    pid = await _project_id(db, user, request)
    project = await db.get(Project, pid)
    conn = await sconn.outbound(db, user.account_id)
    if conn is None or not conn.org_slug:
        msg = _t("incidents.backfill_not_configured", lang)
        return HTMLResponse(f'<div class="text-sm text-error">{msg}</div>')
    if project is None or not project.sentry_project_slug:
        msg = _t("incidents.backfill_no_slug", lang)
        return HTMLResponse(f'<div class="text-sm text-error">{msg}</div>')
    try:
        issues = await wservice.fetch_sentry_issues(
            conn.api_token or "", conn.org_slug, project.sentry_project_slug, query,
            base_url=sconn.effective_base_url(conn),
        )
    except Exception as e:  # network error / bad token / invalid project
        msg = _t("incidents.backfill_error", lang, error=e)
        return HTMLResponse(f'<div class="text-sm text-error">{msg}</div>')
    result = await wservice.backfill_issues(
        db, issues, project.sentry_project_slug, account_id=user.account_id, project_id=pid,
    )
    await db.commit()
    return HTMLResponse(
        f'<div class="text-sm text-success">'
        f'{_t("incidents.backfill_ok", lang, n=result["ingested"], total=result["total"])} '
        f'<a href="/incidents" class="underline">{_t("incidents.reload", lang)}</a></div>'
    )


# ---------- Archive (Registro) ----------

# Page window: 12 weeks with content (spec §2.1). The cut happens at a week boundary
# so "Load more" never duplicates week headers.
_WEEKS_PER_PAGE = 12
# ponytail: per-page fetch cap; >500 closed items in 12 weeks is unrealistic for this
# tool — if it ever hurts, paginate within the week.
_FETCH_CAP = 500


def _iso_week_label(dt: datetime | None) -> str:
    """Return 'YYYY-Www' key for grouping; '' for None (→ the 'No date' group)."""
    if dt is None:
        return ""
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _week_range_label(iso_week_key: str, lang: str) -> str:
    """Localized 'Week of Mon D – Sun D, MMM YYYY' from 'YYYY-Www' (month.* catalog keys)."""
    import datetime as _dt
    year, week = int(iso_week_key[:4]), int(iso_week_key[6:])
    mon = _dt.datetime.fromisocalendar(year, week, 1)
    sun = mon + _dt.timedelta(days=6)
    if mon.month == sun.month:
        return _t("archive.week_same_month", lang, d1=mon.day, d2=sun.day,
                  month=_t(f"month.{sun.month}", lang), year=sun.year)
    return _t("archive.week_cross_month", lang, d1=mon.day, m1=_t(f"month.{mon.month}", lang),
              d2=sun.day, m2=_t(f"month.{sun.month}", lang), year=sun.year)


@router.get("/archive", response_class=HTMLResponse)
async def registro_page(
    request: Request,
    q: str | None = None,
    scope: str | None = None,
    item_type: str | None = None,
    before: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    import datetime as _dt
    from itertools import groupby

    from app.items.search import search_items

    lang = resolve_lang(request)
    project = await resolve_current_project(db, user, request)
    pid = project.id if project else uuid.UUID(int=0)

    base = select(Item).where(
        Item.project_id == pid,
        Item.status.in_(["done", "discarded"]),
    )
    if q:
        ids = [uuid.UUID(r["id"]) for r in await search_items(db, q, project_id=pid, limit=300)]
        base = base.where(Item.id.in_(ids)) if ids else base.where(Item.id == uuid.UUID(int=0))
    if scope:
        scope_row = await db.scalar(select(Scope).where(Scope.name == scope, Scope.project_id == pid))
        if scope_row:
            base = base.where(Item.scope_id == scope_row.id)
    if item_type:
        base = base.where(Item.type == item_type)

    before_dt: datetime | None = None
    if before:
        try:
            before_dt = _dt.datetime.fromisoformat(before)
            if before_dt.tzinfo is None:
                before_dt = before_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            before_dt = None

    dated = base.where(Item.closed_at.isnot(None))
    if before_dt:
        dated = dated.where(Item.closed_at < before_dt)
    dated = dated.order_by(Item.closed_at.desc()).limit(_FETCH_CAP)
    items = list((await db.execute(dated)).scalars().all())

    # Group by ISO week and cut the page at a week boundary (12 weeks).
    groups_all = [
        (key, list(grp)) for key, grp in groupby(items, key=lambda i: _iso_week_label(i.closed_at))
    ]
    page = groups_all[:_WEEKS_PER_PAGE]
    has_more_dated = len(groups_all) > _WEEKS_PER_PAGE or len(items) == _FETCH_CAP

    next_before: str | None = None
    if has_more_dated and page:
        # Monday 00:00 UTC of the oldest included week: everything strictly
        # earlier belongs to previous weeks → no duplicated headers.
        last_key = page[-1][0]
        y, w = int(last_key[:4]), int(last_key[6:])
        next_before = _dt.datetime.fromisocalendar(y, w, 1).replace(
            tzinfo=timezone.utc
        ).isoformat()

    week_groups: list[tuple[str, str, list[Item]]] = [
        (key, _week_range_label(key, lang), grp) for key, grp in page
    ]

    # "No date" (legacy terminal items without closed_at): only on the last page.
    if not has_more_dated:
        undated = list((await db.execute(
            base.where(Item.closed_at.is_(None)).order_by(Item.created_at.desc())
        )).scalars().all())
        if undated:
            week_groups.append(("", _t("archive.no_date", lang), undated))

    page_items = [i for _, _, grp in week_groups for i in grp]

    # Batch-fetch close events (avoids N+1)
    item_ids = [i.id for i in page_items]
    events = list((await db.execute(
        select(ItemEvent).where(ItemEvent.item_id.in_(item_ids), ItemEvent.action == "closed")
        .order_by(ItemEvent.created_at.desc())
    )).scalars().all())
    # Most-recent close event per item
    close_event: dict[str, ItemEvent] = {}
    for ev in events:
        key = str(ev.item_id)
        if key not in close_event:
            close_event[key] = ev

    # Scope name map
    scope_ids = {i.scope_id for i in page_items if i.scope_id}
    scope_rows = list((await db.execute(select(Scope).where(Scope.id.in_(scope_ids)))).scalars().all())
    scope_map = {str(s.id): s.name for s in scope_rows}

    scopes = list((await db.execute(
        select(Scope).where(Scope.project_id == pid).order_by(Scope.name)
    )).scalars().all())

    ctx = {
        "user": user,
        "project": project,
        "week_groups": week_groups,
        "close_event": close_event,
        "scope_map": scope_map,
        "scopes": scopes,
        "filters": {"q": q, "scope": scope, "item_type": item_type},
        "next_before": next_before,
        "is_load_more": before_dt is not None,
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/archive_rows.html", ctx)
    return templates.TemplateResponse(request, "archive.html", ctx)


@router.get("/ui/archive/summary", response_class=HTMLResponse)
async def ui_registro_summary(
    request: Request,
    week: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.ai.llm import LLMUnavailable, summarize_closed

    pid = await _project_id(db, user, request)

    # Parse week key YYYY-Www → date range
    try:
        import datetime as _dt
        year, wnum = int(week[:4]), int(week[6:])
        mon = _dt.datetime.fromisocalendar(year, wnum, 1).replace(tzinfo=timezone.utc)
        sun = mon + _dt.timedelta(days=6, hours=23, minutes=59, seconds=59)
    except (ValueError, IndexError):
        return HTMLResponse(
            f'<p class="text-sm text-error">{_t("archive.invalid_week", resolve_lang(request))}</p>',
            status_code=400,
        )

    items = list((await db.execute(
        select(Item).where(
            Item.project_id == pid,
            Item.status.in_(["done", "discarded"]),
            Item.closed_at >= mon,
            Item.closed_at <= sun,
        )
    )).scalars().all())

    item_ids = [i.id for i in items]
    events = list((await db.execute(
        select(ItemEvent).where(ItemEvent.item_id.in_(item_ids), ItemEvent.action == "closed")
        .order_by(ItemEvent.created_at.desc())
    )).scalars().all())
    close_event = {str(ev.item_id): ev for ev in reversed(events)}

    items_data = [
        {
            "title": i.title,
            "type": i.type,
            "status": i.status,
            "reason": (close_event[str(i.id)].payload or {}).get("reason")
            if str(i.id) in close_event else None,
        }
        for i in items
    ]

    try:
        summary_md = await summarize_closed(items_data, lang=resolve_lang(request))
    except LLMUnavailable:
        summary_md = None

    return templates.TemplateResponse(
        request, "partials/archive_summary.html",
        {"summary_md": summary_md, "week": week},
    )


@router.post("/ui/admin/tokens", response_class=HTMLResponse)
async def ui_create_token(
    request: Request,
    name: str = Form(...),
    scopes: str = Form("write"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    if not user.is_superadmin:
        msg = _t("admin.unauthorized", resolve_lang(request))
        return HTMLResponse(f'<div class="text-sm text-error">{msg}</div>', status_code=403)
    from app.auth.service import create_api_token

    if scopes not in ("read", "write"):
        scopes = "write"
    _tok, raw = await create_api_token(db, name, scopes, user.id)
    return templates.TemplateResponse(
        request, "partials/token_created.html", {"raw": raw, "name": name, "scopes": scopes}
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    if not user.is_superadmin:
        return RedirectResponse("/", status_code=303)

    users = list((await db.execute(select(User).order_by(User.created_at))).scalars().all())
    tokens = list((await db.execute(
        select(ApiToken).where(ApiToken.revoked_at.is_(None)).order_by(ApiToken.created_at.desc())
    )).scalars().all())
    scopes = list((await db.execute(
        select(Scope).order_by(Scope.display_order, Scope.name)
    )).scalars().all())
    runs = list((await db.execute(
        select(AgentRun).order_by(AgentRun.created_at.desc()).limit(20)
    )).scalars().all())

    return templates.TemplateResponse(
        request,
        "admin.html",
        {"user": user, "users": users, "tokens": tokens, "scopes": scopes, "runs": runs},
    )


# ---------- Legacy Spanish slugs (pre-2026-07-16) — 301 to the English routes ----------

_LEGACY_SLUGS = {
    "/prioridad": "/priority",
    "/hilos": "/threads",
    "/incidentes": "/incidents",
    "/registro": "/archive",
}


def _make_legacy_redirect(new_path: str):
    async def _redirect(request: Request) -> RedirectResponse:
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"{new_path}{query}", status_code=301)
    return _redirect


for _old_path, _new_path in _LEGACY_SLUGS.items():
    router.add_api_route(
        _old_path, _make_legacy_redirect(_new_path), methods=["GET"], include_in_schema=False
    )


@router.get("/hilos/{thread_id}", include_in_schema=False)
async def _legacy_thread_detail(thread_id: uuid.UUID) -> RedirectResponse:
    return RedirectResponse(f"/threads/{thread_id}", status_code=301)
