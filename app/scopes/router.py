import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import api_or_session_user, current_project_id, require_write
from app.database import get_db
from app.scopes import service
from app.scopes.models import Scope

router = APIRouter(prefix="/scopes", tags=["scopes"])


class ScopeCreate(BaseModel):
    name: str
    description: str | None = None
    color: str | None = None
    source_repo: str | None = None
    display_order: int = 0


class ScopePatch(BaseModel):
    description: str | None = None
    color: str | None = None
    archived: bool | None = None
    display_order: int | None = None


class ScopeOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    color: str | None
    source_repo: str | None
    archived: bool
    display_order: int

    model_config = {"from_attributes": True}


@router.get("", response_model=list[ScopeOut])
async def list_scopes(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10_000),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(api_or_session_user),
    pid: uuid.UUID = Depends(current_project_id),
):
    result = await db.execute(
        select(Scope).where(Scope.project_id == pid)
        .order_by(Scope.display_order, Scope.name, Scope.id)
        .offset(offset).limit(limit)
    )
    return result.scalars().all()


@router.post("", response_model=ScopeOut, status_code=201)
async def create_scope(
    body: ScopeCreate,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_write),
    pid: uuid.UUID = Depends(current_project_id),
):
    try:
        scope = await service.create_scope(db, {**body.model_dump(), "project_id": pid})
        await db.commit()
        await db.refresh(scope)
    except service.ScopeError as e:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Area '{body.name}' already exists in this project")
    return scope


@router.patch("/{scope_id}", response_model=ScopeOut)
async def patch_scope(
    scope_id: uuid.UUID,
    body: ScopePatch,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_write),
    pid: uuid.UUID = Depends(current_project_id),
):
    existing = await db.get(Scope, scope_id)
    if existing is None or existing.project_id != pid:
        raise HTTPException(status_code=404, detail="Area not found")
    try:
        scope = await service.update_scope(db, scope_id, body.model_dump(exclude_none=True))
        await db.commit()
        await db.refresh(scope)
    except service.ScopeError as e:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(e)) from e
    return scope
