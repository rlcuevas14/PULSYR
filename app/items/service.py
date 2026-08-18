"""Item mutation logic, shared by the JSON API, the UI and the MCP.

Centralizes transition validation (lifecycle) and auditing (ItemEvent),
so that UI / REST / MCP never diverge.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import COMMENT_KINDS, EFFORTS, PRIORITIES, TERMINAL
from app.items import graph
from app.items.lifecycle import valid_transition
from app.items.models import Item, ItemComment, ItemEvent
from app.scopes.models import Scope

# Human priority rank for the "priority" ordering (p0 first, no priority last).
_PRIORITY_RANK: dict[str | None, int] = {"p0": 0, "p1": 1, "p2": 2, "p3": 3, None: 9}
_TOPOLOGICAL_CANDIDATE_LIMIT = 1000


class TransitionError(ValueError):
    """Invalid status transition."""


class ItemUpdateError(ValueError):
    """Invalid item metadata or comment mutation."""


async def get_item(db: AsyncSession, item_id: uuid.UUID) -> Item | None:
    result = await db.execute(select(Item).where(Item.id == item_id))
    return result.scalar_one_or_none()


async def apply_transition(db: AsyncSession, item: Item, to_status: str, actor: str) -> Item:
    """Change the status, validating the transition. Transitions to terminal states
    must go through close_item (which asks for a reason); they are rejected here."""
    if to_status in TERMINAL:
        raise TransitionError(
            f"'{to_status}' is terminal — use close/discard (with a reason), not a direct transition."
        )
    if not valid_transition(item.status, to_status):
        raise TransitionError(f"Invalid transition: {item.status} → {to_status}")
    old = item.status
    if old != to_status:
        item.status = to_status
        # Leaving a terminal state (done→backlog) is a reopen: without clearing closed_at
        # the item would show up in the backlog AND in the archive at the same time.
        if old in TERMINAL:
            item.closed_at = None
        await db.flush()
        db.add(ItemEvent(
            item_id=item.id, actor=actor, action="status_changed",
            payload={"from": old, "to": to_status},
        ))
    return item


def _merge_source_ref(item: Item, key: str, value: Any) -> None:
    refs = dict(item.source_refs) if isinstance(item.source_refs, dict) else {}
    refs[key] = value
    item.source_refs = refs


async def close_item(
    db: AsyncSession,
    item: Item,
    status: str,
    reason: str | None,
    actor: str,
    commit_sha: str | None = None,
) -> list[dict[str, Any]]:
    """Close an item (done|discarded). Returns the list of items that this closure
    left unblocked (blocking is derived from the graph)."""
    if status not in TERMINAL:
        raise TransitionError("status must be 'done' or 'discarded'")
    if not valid_transition(item.status, status):
        raise TransitionError(f"Invalid transition: {item.status} → {status}")

    item.status = status
    item.closed_at = datetime.now(timezone.utc)
    if commit_sha:
        _merge_source_ref(item, "commit_sha", commit_sha)
    db.add(ItemEvent(
        item_id=item.id, actor=actor, action="closed",
        payload={"status": status, "reason": reason, "commit_sha": commit_sha},
    ))
    await db.flush()

    # Blocking is derived: after closing, compute which targets are left with no open blocker.
    unblocked = await graph.unblocked_by(db, item.id)
    for t in unblocked:
        db.add(ItemEvent(
            item_id=uuid.UUID(t["id"]), actor=actor, action="unblocked_by",
            payload={"by_item": str(item.id), "by_title": item.title},
        ))
    return unblocked


async def reopen_item(db: AsyncSession, item: Item, actor: str) -> Item:
    if item.status not in TERMINAL:
        raise TransitionError("Only done/discarded items can be reopened.")
    old = item.status
    item.status = "backlog"
    item.closed_at = None
    await db.flush()
    db.add(ItemEvent(
        item_id=item.id, actor=actor, action="reopened",
        payload={"from": old, "to": "backlog"},
    ))
    return item


async def set_priority(db: AsyncSession, item: Item, priority: str | None, actor: str) -> Item:
    """Set the human priority. What the human declared is recorded
    in priority_declared (it wins over the AI judgment in ordering/matrix)."""
    item.priority = priority
    item.priority_declared = priority
    await db.flush()
    db.add(ItemEvent(
        item_id=item.id, actor=actor, action="priority_changed",
        payload={"priority": priority},
    ))
    return item


async def update_item(
    db: AsyncSession,
    item: Item,
    changes: dict[str, Any],
    actor: str,
) -> Item:
    """Update non-lifecycle fields and emit a before/after audit receipt.

    Presence in ``changes`` is significant: nullable fields can be explicitly cleared.
    Status is deliberately excluded and must use the lifecycle services.
    """
    allowed = {
        "title", "summary_md", "priority", "impact_ai", "effort_ai",
        "stale_risk", "agent_ready",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ItemUpdateError(f"Fields are not editable: {', '.join(sorted(unknown))}.")
    if not changes:
        raise ItemUpdateError("Provide at least one field to update.")

    normalized = dict(changes)
    if "title" in normalized:
        title = str(normalized["title"] or "").strip()
        if not title:
            raise ItemUpdateError("title cannot be empty.")
        normalized["title"] = title[:300]
    if "summary_md" in normalized and normalized["summary_md"] is not None:
        normalized["summary_md"] = str(normalized["summary_md"]).strip() or None
    if "priority" in normalized:
        priority = normalized["priority"]
        if priority is not None and priority not in PRIORITIES:
            raise ItemUpdateError(
                f"invalid priority '{priority}'; use one of: {', '.join(PRIORITIES)} (or null)."
            )
    if "impact_ai" in normalized:
        impact = normalized["impact_ai"]
        if impact is not None and (
            not isinstance(impact, int) or isinstance(impact, bool) or not 1 <= impact <= 5
        ):
            raise ItemUpdateError("impact_ai must be an integer 1-5 (or null).")
    if "effort_ai" in normalized:
        effort = normalized["effort_ai"]
        if effort is not None and effort not in EFFORTS:
            raise ItemUpdateError(
                f"invalid effort_ai '{effort}'; use one of: {', '.join(EFFORTS)} (or null)."
            )
    for field in ("stale_risk", "agent_ready"):
        if field in normalized and not isinstance(normalized[field], bool):
            raise ItemUpdateError(f"{field} must be a boolean.")

    before = {field: getattr(item, field) for field in normalized}
    priority_present = "priority" in normalized
    priority = normalized.pop("priority", None)
    if priority_present:
        await set_priority(db, item, priority, actor)
    for field, value in normalized.items():
        setattr(item, field, value)
    await db.flush()
    fields = list(changes)
    db.add(ItemEvent(
        item_id=item.id,
        actor=actor,
        action="item_updated",
        payload={
            "fields": fields,
            "before": before,
            "after": {field: getattr(item, field) for field in fields},
        },
    ))
    return item


async def add_comment(
    db: AsyncSession,
    item: Item,
    body_md: str,
    kind: str,
    actor: str,
) -> ItemComment:
    body = (body_md or "").strip()
    if not body:
        raise ItemUpdateError("Comment cannot be empty.")
    if kind not in COMMENT_KINDS:
        raise ItemUpdateError(
            f"invalid comment kind '{kind}'; use one of: {', '.join(COMMENT_KINDS)}."
        )
    comment = ItemComment(item_id=item.id, author=actor, body_md=body, kind=kind)
    db.add(comment)
    await db.flush()
    db.add(ItemEvent(
        item_id=item.id,
        actor=actor,
        action="comment_added",
        payload={"comment_id": str(comment.id), "kind": kind},
    ))
    return comment


def iso_week_bounds(week: str) -> tuple[datetime, datetime]:
    """Parse YYYY-Www into a half-open UTC interval."""
    try:
        year = int(week[:4])
        number = int(week[6:])
        if len(week) != 8 or week[4:6] != "-W":
            raise ValueError
        start = datetime.fromisocalendar(year, number, 1).replace(tzinfo=timezone.utc)
    except (ValueError, IndexError) as exc:
        raise ItemUpdateError("week must use ISO format YYYY-Www.") from exc
    return start, start + timedelta(days=7)


async def list_archive_items(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    week: str | None = None,
    status: str | None = None,
    scope: Any = None,
    type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Item], dict[uuid.UUID, ItemEvent], int]:
    """Return a stable, paginated archive projection plus latest close receipts."""
    if status is not None and status not in TERMINAL:
        raise ItemUpdateError("status must be done or discarded.")
    scope_id = await _resolve_scope_id(db, scope, project_id=project_id)
    if scope is not None and scope_id is None:
        return [], {}, 0
    query = select(Item).where(
        Item.project_id == project_id,
        Item.status.in_([status] if status else list(TERMINAL)),
    )
    if scope_id is not None:
        query = query.where(Item.scope_id == scope_id)
    if type is not None:
        query = query.where(Item.type == type)
    if week is not None:
        start, end = iso_week_bounds(week)
        query = query.where(Item.closed_at >= start, Item.closed_at < end)
    total = int(await db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = list((await db.execute(
        query.order_by(Item.closed_at.desc().nullslast(), Item.id).offset(offset).limit(limit)
    )).scalars().all())
    ids = [item.id for item in rows]
    events: dict[uuid.UUID, ItemEvent] = {}
    if ids:
        receipts = list((await db.execute(
            select(ItemEvent)
            .where(ItemEvent.item_id.in_(ids), ItemEvent.action == "closed")
            .order_by(ItemEvent.created_at.desc())
        )).scalars().all())
        for event in receipts:
            events.setdefault(event.item_id, event)
    return rows, events, total


async def touch_embedding_available(db: AsyncSession) -> bool:
    """True if the embedding column exists and has at least one non-NULL value (semantic layer)."""
    try:
        result = await db.execute(text("SELECT 1 FROM items WHERE embedding IS NOT NULL LIMIT 1"))
        return result.first() is not None
    except Exception:
        return False


# ---------- Listing + ordering (DUP-1) ----------
#
# Single implementation of item listing with filters + ordering, consumed by REST,
# UI and MCP. Topological ordering uses the graph (graph.topological_order).

async def _resolve_scope_id(
    db: AsyncSession,
    scope: Any,
    *,
    project_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """Accept a scope as a uuid.UUID or as a name (str). Returns the id, or None if it doesn't exist."""
    if scope is None:
        return None
    if isinstance(scope, uuid.UUID):
        return scope
    # str: can be a UUID as text or the scope name.
    try:
        return uuid.UUID(str(scope))
    except (ValueError, AttributeError):
        query = select(Scope).where(Scope.name == str(scope))
        if project_id is not None:
            query = query.where(Scope.project_id == project_id)
        row = await db.scalar(query)
        return row.id if row else None


def _topo_rank(items: list[Item], edges: list[tuple[str, str, str]]) -> dict[str, int]:
    """Map id→position according to the topological order of the precedence graph."""
    ids = [str(i.id) for i in items]
    if not ids:
        return {}
    impact = {str(i.id): (i.impact_ai or 0) for i in items}
    result = graph.topological_order(ids, edges, impact)
    return {item_id: rank for rank, item_id in enumerate(result["order"])}


async def _topo_order_ids(db: AsyncSession, items: list[Item]) -> dict[str, int]:
    """Compute the topological rank, loading the arcs internal to the item set."""
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
    return _topo_rank(items, edges)


def _order_items(items: list[Item], order: str, topo_rank: dict[str, int] | None) -> list[Item]:
    if order == "impact":
        return sorted(items, key=lambda i: (-(i.impact_ai or 0), i.effort_ai or "ZZ"))
    if order == "priority":
        return sorted(items, key=lambda i: (_PRIORITY_RANK.get(i.priority, 9), -(i.impact_ai or 0)))
    if order == "topological" and topo_rank is not None:
        return sorted(items, key=lambda i: topo_rank.get(str(i.id), 1_000_000))
    return sorted(items, key=lambda i: i.created_at, reverse=True)


def _apply_item_filters(
    q: "Select[Any]",
    *,
    project_id: uuid.UUID | None,
    scope_id: uuid.UUID | None,
    statuses: list[str] | None,
    type: str | None,
    stale_risk: bool | None,
    quickwins: bool,
) -> "Select[Any]":
    if project_id is not None:
        q = q.where(Item.project_id == project_id)
    if scope_id is not None:
        q = q.where(Item.scope_id == scope_id)
    if statuses:
        q = q.where(Item.status.in_(statuses))
    if type:
        q = q.where(Item.type == type)
    if stale_risk is not None:
        q = q.where(Item.stale_risk == stale_risk)
    if quickwins:
        q = q.where(Item.impact_ai >= 4, Item.effort_ai.in_(["XS", "S"]))
    return q


async def list_items(
    db: AsyncSession,
    *,
    project_id: uuid.UUID | None = None,
    scope: Any = None,
    statuses: list[str] | None = None,
    type: str | None = None,
    order: str = "impact",
    quickwins: bool = False,
    stale_risk: bool | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[Item]:
    """List items with filters + ordering. Single implementation (REST/UI/MCP).

    Args:
        scope: uuid.UUID or scope name (str). None = all scopes.
        statuses: list of statuses to include. None = all.
        type: item type (a single one). None = all.
        order: "impact" | "priority" | "topological" | "recent".
        quickwins: if True, only high-impact (>=4), low-effort (XS/S) items.
        stale_risk: filter by the staleness-risk flag.
        limit / offset: pagination. limit=None fetches the whole filtered set,
            except topological ordering, which always uses the candidate ceiling.

    Common orderings execute in PostgreSQL before pagination. Topological order
    needs the relationship graph, so it is computed over a bounded candidate set
    and pagination is applied only after that ordering.
    """
    scope_id = await _resolve_scope_id(db, scope, project_id=project_id)
    # If a nonexistent scope was requested by name, the result is empty (not "all").
    if scope is not None and scope_id is None:
        return []

    q = _apply_item_filters(
        select(Item),
        project_id=project_id, scope_id=scope_id, statuses=statuses, type=type,
        stale_risk=stale_risk, quickwins=quickwins,
    )
    canonical_order = {
        "impacto": "impact",
        "prioridad": "priority",
        "topologico": "topological",
        "reciente": "recent",
    }.get(order, order)

    if canonical_order == "topological":
        q = q.order_by(Item.created_at.desc(), Item.id).limit(_TOPOLOGICAL_CANDIDATE_LIMIT)
        items = list((await db.execute(q)).scalars().all())
        ordered = _order_items(items, canonical_order, await _topo_order_ids(db, items))
        end = None if limit is None else offset + limit
        return ordered[offset:end]

    if canonical_order == "priority":
        q = q.order_by(
            Item.priority.asc().nullslast(), Item.impact_ai.desc().nullslast(), Item.id
        )
    elif canonical_order == "impact":
        q = q.order_by(
            Item.impact_ai.desc().nullslast(), Item.effort_ai.asc().nullslast(), Item.id
        )
    else:
        q = q.order_by(Item.created_at.desc(), Item.id)

    if offset:
        q = q.offset(offset)
    if limit is not None:
        q = q.limit(limit)
    return list((await db.execute(q)).scalars().all())
