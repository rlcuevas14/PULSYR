"""Scope (area) service — shared by REST, UI, MCP, and webhooks."""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.scopes.models import Scope


class ScopeError(ValueError):
    pass


async def resolve_scope(
    db: AsyncSession,
    name: str,
    *,
    create: bool,
    project_id: uuid.UUID | None = None,
    source_repo: str | None = None,
) -> Scope:
    """Resolve a scope by name (case-insensitive). Creates it if create=True and missing.

    When project_id is given, the lookup and creation are scoped to that project.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        raise ScopeError("Area name cannot be empty.")

    q = select(Scope).where(func.lower(Scope.name) == cleaned.lower())
    if project_id is not None:
        q = q.where(Scope.project_id == project_id)

    scope = (await db.execute(q)).scalar_one_or_none()
    if scope is not None:
        return scope

    if not create:
        raise ScopeError(
            f"Area '{cleaned}' does not exist. Use pulsyr_areas to see available areas."
        )

    scope = Scope(name=cleaned[:60], source_repo=source_repo, project_id=project_id)
    db.add(scope)
    await db.flush()
    return scope


async def create_scope(db: AsyncSession, data: dict[str, Any]) -> Scope:
    name = (data.get("name") or "").strip()
    if not name:
        raise ScopeError("Area name cannot be empty.")
    scope = Scope(**{**data, "name": name})
    db.add(scope)
    await db.flush()
    return scope


async def update_scope(db: AsyncSession, scope_id: uuid.UUID, changes: dict[str, Any]) -> Scope:
    scope = (await db.execute(select(Scope).where(Scope.id == scope_id))).scalar_one_or_none()
    if scope is None:
        raise ScopeError("Area not found.")
    for field, value in changes.items():
        setattr(scope, field, value)
    await db.flush()
    return scope
