import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.deps import api_or_session_user, current_project_id, require_owner, require_write
from app.auth.models import ApiToken, User
from app.database import get_db
from app.enums import (
    COMMENT_KINDS,
    EFFORTS,
    ITEM_STATUSES,
    ITEM_TYPES,
    PRIORITIES,
)
from app.items.models import Item, ItemComment
from app.scopes.models import Scope

router = APIRouter(prefix="/items", tags=["items"])

# Tipos cerrados (Literal) derivados del módulo único de enums. Pydantic valida en el
# borde (422 automático con la lista de valores válidos) antes de tocar la BD.
ItemTypeLit = Literal[ITEM_TYPES]  # type: ignore[valid-type]
ItemStatusLit = Literal[ITEM_STATUSES]  # type: ignore[valid-type]
PriorityLit = Literal[PRIORITIES]  # type: ignore[valid-type]
EffortLit = Literal[EFFORTS]  # type: ignore[valid-type]
CommentKindLit = Literal[COMMENT_KINDS]  # type: ignore[valid-type]


class ItemCreate(BaseModel):
    scope_id: uuid.UUID
    title: str
    type: ItemTypeLit
    summary_md: str | None = None
    status: ItemStatusLit = "backlog"
    priority: PriorityLit | None = None
    effort_declared: str | None = None
    priority_declared: str | None = None
    trigger_text: str | None = None
    dependencies: str | None = None
    stale_risk: bool = False


class ItemPatch(BaseModel):
    title: str | None = None
    summary_md: str | None = None
    status: ItemStatusLit | None = None
    priority: PriorityLit | None = None
    impact_ai: int | None = None
    effort_ai: EffortLit | None = None
    stale_risk: bool | None = None
    agent_ready: bool | None = None


class CommentCreate(BaseModel):
    body_md: str
    kind: CommentKindLit = "comment"


class CloseItem(BaseModel):
    status: str  # "done" | "discarded"
    reason: str | None = None
    commit_sha: str | None = None


class ItemOut(BaseModel):
    id: uuid.UUID
    scope_id: uuid.UUID
    title: str
    summary_md: str | None
    type: str
    status: str
    priority: str | None
    effort_ai: str | None
    impact_ai: int | None
    stale_risk: bool
    agent_ready: bool
    origen: str
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    model_config = {"from_attributes": True}


class ItemDetail(ItemOut):
    impact_rationale: str | None
    effort_declared: str | None
    priority_declared: str | None
    trigger_text: str | None
    dependencies: str | None
    source_refs: Any = None
    events: list[dict]
    comments: list[dict]
    enrichments: list[dict]


def _actor(auth) -> str:
    if isinstance(auth, User):
        return auth.email
    if isinstance(auth, ApiToken):
        return f"token:{auth.name}"
    return "unknown"


def _require_item(item: "Item | None", pid: uuid.UUID) -> Item:
    """404 unless the item exists and belongs to the request's project (account isolation)."""
    if item is None or item.project_id != pid:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


# PERF-04: paginación con tope duro para no devolver el backlog entero sin querer.
_LIST_DEFAULT_LIMIT = 50
_LIST_MAX_LIMIT = 200


@router.get("", response_model=list[ItemOut])
async def list_items(
    scope_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None),
    type: str | None = Query(None),
    stale_risk: bool | None = Query(None),
    order: str = Query("reciente"),
    limit: int = Query(_LIST_DEFAULT_LIMIT, ge=1, le=_LIST_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(api_or_session_user),
    pid: uuid.UUID = Depends(current_project_id),
):
    """DUP-1: consume la implementación única `items.service.list_items` (REST/UI/MCP).

    PERF-04: `limit` por defecto 50, tope duro 200; `offset` para paginar.
    Scoped to the request's project (account isolation).
    """
    from app.items import service

    items = await service.list_items(
        db,
        project_id=pid,
        scope=scope_id,
        statuses=[status] if status else None,
        type=type,
        order=order,
        stale_risk=stale_risk,
        limit=limit,
        offset=offset,
    )
    return items


@router.post("", response_model=ItemOut, status_code=201)
async def create_item(
    body: ItemCreate,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_or_session_user),
    _=Depends(require_write),
    pid: uuid.UUID = Depends(current_project_id),
):
    # The scope must belong to the request's project (no cross-project item creation).
    scope = await db.get(Scope, body.scope_id)
    if scope is None or scope.project_id != pid:
        raise HTTPException(status_code=422, detail="Scope does not belong to this project")
    item = Item(**body.model_dump(), project_id=pid, created_by=_actor(auth), origen="human")
    db.add(item)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=422, detail=f"Invalid data: {e.orig}") from e
    await db.refresh(item)
    return item


class ImportRequest(BaseModel):
    path: str | None = None
    directory: str | None = None


@router.post("/import/digest")
async def import_digest(
    body: ImportRequest,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_owner),
):
    target = body.path or body.directory
    if not target:
        raise HTTPException(status_code=422, detail="Proporcionar 'path' o 'directory'")

    p = Path(target)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Ruta no encontrada: {target}")

    from app.items.importer import import_directory, import_jsonl
    if p.is_dir():
        result = await import_directory(db, p)
    else:
        result = await import_jsonl(db, p)

    return result


_SEARCH_MAX_LIMIT = 200


@router.get("/search")
async def search_items(
    q: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=_SEARCH_MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(api_or_session_user),
    pid: uuid.UUID = Depends(current_project_id),
):
    """DUP-1: consume la implementación única del FTS (`items.search.search_items`).

    PERF-04: `limit` por defecto 50, tope duro 200. Scoped to the request's project.
    """
    from app.items import search

    return await search.search_items(db, q, limit=limit, project_id=pid)


@router.get("/{item_id}", response_model=ItemDetail)
async def get_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(api_or_session_user),
    pid: uuid.UUID = Depends(current_project_id),
):
    result = await db.execute(
        select(Item)
        .where(Item.id == item_id)
        .options(
            selectinload(Item.events),
            selectinload(Item.comments),
            selectinload(Item.enrichments),
        )
    )
    item = _require_item(result.scalar_one_or_none(), pid)

    def _event_dict(e):
        return {"id": str(e.id), "actor": e.actor, "action": e.action,
                "payload": e.payload, "created_at": e.created_at.isoformat()}

    def _comment_dict(c):
        return {"id": str(c.id), "author": c.author, "body_md": c.body_md,
                "kind": c.kind, "created_at": c.created_at.isoformat()}

    def _enrichment_dict(en):
        return {"id": str(en.id), "model": en.model, "effort": en.effort,
                "impact": en.impact, "rationale": en.rationale,
                "created_at": en.created_at.isoformat()}

    return ItemDetail(
        **{c.key: getattr(item, c.key) for c in Item.__table__.columns
           if c.key not in ("events", "comments", "enrichments")},
        events=[_event_dict(e) for e in item.events],
        comments=[_comment_dict(c) for c in item.comments],
        enrichments=[_enrichment_dict(e) for e in item.enrichments],
    )


@router.patch("/{item_id}", response_model=ItemOut)
async def patch_item(
    item_id: uuid.UUID,
    body: ItemPatch,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_or_session_user),
    _=Depends(require_write),
    pid: uuid.UUID = Depends(current_project_id),
):
    from app.items import service

    item = _require_item(await service.get_item(db, item_id), pid)

    changes = body.model_dump(exclude_none=True)
    actor = _actor(auth)

    # status: pasa por el validador de transiciones (terminales → usar /close).
    if "status" in changes:
        try:
            await service.apply_transition(db, item, changes.pop("status"), actor)
        except service.TransitionError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

    # priority: registra priority_declared (lo declarado por el humano).
    if "priority" in changes:
        await service.set_priority(db, item, changes.pop("priority"), actor)

    # resto de campos: asignación directa.
    for field, value in changes.items():
        setattr(item, field, value)

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=422, detail=f"Invalid data: {e.orig}") from e
    await db.refresh(item)
    return item


@router.get("/{item_id}/comments/{comment_id}")
async def get_comment(
    item_id: uuid.UUID,
    comment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(api_or_session_user),
    pid: uuid.UUID = Depends(current_project_id),
):
    """Lectura de un comentario individual. Comentarios son append-only — no existe PATCH."""
    _require_item(await db.get(Item, item_id), pid)
    result = await db.execute(
        select(ItemComment).where(
            ItemComment.id == comment_id,
            ItemComment.item_id == item_id,
        )
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail="Comment not found")
    return {
        "id": str(comment.id),
        "author": comment.author,
        "body_md": comment.body_md,
        "kind": comment.kind,
        "created_at": comment.created_at.isoformat(),
    }


@router.post("/{item_id}/comments", status_code=201)
async def add_comment(
    item_id: uuid.UUID,
    body: CommentCreate,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_or_session_user),
    _=Depends(require_write),
    pid: uuid.UUID = Depends(current_project_id),
):
    _require_item(await db.get(Item, item_id), pid)

    comment = ItemComment(
        item_id=item_id,
        author=_actor(auth),
        body_md=body.body_md,
        kind=body.kind,
    )
    db.add(comment)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=422, detail=f"Invalid data: {e.orig}") from e
    await db.refresh(comment)
    return {"id": str(comment.id), "created_at": comment.created_at.isoformat()}


@router.post("/{item_id}/close")
async def close_item(
    item_id: uuid.UUID,
    body: CloseItem,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_or_session_user),
    _=Depends(require_write),
    pid: uuid.UUID = Depends(current_project_id),
):
    from app.items import service

    item = _require_item(await service.get_item(db, item_id), pid)

    try:
        unblocked = await service.close_item(
            db, item, body.status, body.reason, _actor(auth), commit_sha=body.commit_sha
        )
    except service.TransitionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    await db.commit()
    await db.refresh(item)
    return {**ItemOut.model_validate(item).model_dump(mode="json"), "unblocked": unblocked}


@router.post("/{item_id}/reopen", response_model=ItemOut)
async def reopen_item_endpoint(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_or_session_user),
    _=Depends(require_write),
    pid: uuid.UUID = Depends(current_project_id),
):
    from app.items import service

    item = _require_item(await service.get_item(db, item_id), pid)
    try:
        await service.reopen_item(db, item, _actor(auth))
    except service.TransitionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    await db.commit()
    await db.refresh(item)
    return item


class RelationshipCreate(BaseModel):
    source_id: uuid.UUID
    target_id: uuid.UUID
    relation: str
    note: str | None = None


async def _both_items_in_project(db: AsyncSession, a: uuid.UUID, b: uuid.UUID, pid: uuid.UUID) -> None:
    for iid in (a, b):
        item = await db.get(Item, iid)
        if item is None or item.project_id != pid:
            raise HTTPException(status_code=404, detail="Item not found")


@router.post("/relationships", status_code=201)
async def create_relationship_endpoint(
    body: RelationshipCreate,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(api_or_session_user),
    _=Depends(require_write),
    pid: uuid.UUID = Depends(current_project_id),
):
    from app.items import relationships

    await _both_items_in_project(db, body.source_id, body.target_id, pid)
    try:
        rel = await relationships.create_relationship(
            db, body.source_id, body.target_id, body.relation, body.note
        )
        await db.commit()
    except relationships.RelationshipError as e:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=422, detail=f"Invalid data: {e.orig}") from e
    return {
        "source_id": str(rel.source_id),
        "target_id": str(rel.target_id),
        "relation": rel.relation,
    }


@router.delete("/relationships/{source_id}/{target_id}/{relation}", status_code=200)
async def delete_relationship_endpoint(
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    relation: str,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(api_or_session_user),
    _=Depends(require_write),
    pid: uuid.UUID = Depends(current_project_id),
):
    from app.items import relationships

    await _both_items_in_project(db, source_id, target_id, pid)
    ok = await relationships.delete_relationship(db, source_id, target_id, relation)
    await db.commit()
    return {"deleted": ok}


@router.get("/{item_id}/graph")
async def item_graph(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(api_or_session_user),
    pid: uuid.UUID = Depends(current_project_id),
):
    from app.items import graph

    _require_item(await db.get(Item, item_id), pid)
    return await graph.subgraph(db, item_id)


@router.post("/{item_id}/enrich", status_code=202)
async def enqueue_enrich(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_or_session_user),
    _=Depends(require_write),
    pid: uuid.UUID = Depends(current_project_id),
):
    from app.jobs.worker import enqueue_job

    _require_item(await db.get(Item, item_id), pid)

    run = await enqueue_job(db, kind="enrich", ref_type="item", ref_id=item_id, project_id=pid)
    return {"run_id": str(run.id), "status": "queued"}


@router.post("/enrich-pending", status_code=202)
async def enqueue_pending_enrich(
    limit: int = Query(200),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_owner),
    pid: uuid.UUID = Depends(current_project_id),
):
    """Encola enriquecimiento para todos los ítems abiertos sin impacto estimado (owner)."""
    from app.jobs.worker import enqueue_job

    rows = await db.execute(
        select(Item.id).where(
            Item.project_id == pid,
            Item.impact_ai.is_(None),
            Item.status.not_in(["done", "discarded"]),
        ).limit(limit)
    )
    ids = [r[0] for r in rows]
    for item_id in ids:
        await enqueue_job(db, kind="enrich", ref_type="item", ref_id=item_id, project_id=pid)
    return {"queued": len(ids)}
