import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.projects.models import Project
from app.scopes.models import Scope


class ProjectError(ValueError):
    pass


def _slugify(name: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:60] or "project"


async def list_projects(
    db: AsyncSession, account_id: uuid.UUID, include_archived: bool = False
) -> list[Project]:
    q = select(Project).where(Project.account_id == account_id)
    if not include_archived:
        q = q.where(Project.archived_at.is_(None))
    return list((await db.execute(q.order_by(Project.created_at))).scalars().all())


async def get_by_id(
    db: AsyncSession, project_id: uuid.UUID, account_id: uuid.UUID | None = None
) -> Project | None:
    q = select(Project).where(Project.id == project_id)
    if account_id is not None:
        q = q.where(Project.account_id == account_id)
    return (await db.execute(q)).scalar_one_or_none()


async def get_by_slug(db: AsyncSession, slug: str, account_id: uuid.UUID) -> Project | None:
    return (await db.execute(
        select(Project).where(Project.slug == slug, Project.account_id == account_id)
    )).scalar_one_or_none()


async def create_project(
    db: AsyncSession,
    name: str,
    account_id: uuid.UUID,
    slug: str | None = None,
    description: str | None = None,
    color: str | None = None,
) -> Project:
    from app.accounts.plans import ensure_project_capacity

    await ensure_project_capacity(db, account_id)
    name = name.strip()
    if not name:
        raise ProjectError("Project name cannot be empty.")
    final_slug = (slug or _slugify(name)).strip()
    if not final_slug:
        raise ProjectError("Project slug cannot be empty.")
    existing = await get_by_slug(db, final_slug, account_id)
    if existing:
        raise ProjectError(f"A project with slug '{final_slug}' already exists.")
    project = Project(
        name=name, slug=final_slug, description=description, color=color, account_id=account_id
    )
    db.add(project)
    await db.flush()
    # A project must be usable by a human from minute one: the new-item modal
    # requires a scope and the UI has no screen to create one (only MCP/REST do).
    db.add(Scope(name="General", project_id=project.id))
    await db.flush()
    return project


async def update_project(db: AsyncSession, project: Project, changes: dict) -> Project:
    for field, value in changes.items():
        if hasattr(project, field):
            setattr(project, field, value)
    await db.flush()
    return project


async def rename_project(db: AsyncSession, project: Project, name: str) -> Project:
    """Rename a starter project and keep its human-readable slug aligned."""
    name = name.strip()
    if not name:
        raise ProjectError("Project name cannot be empty.")
    slug = _slugify(name)
    existing = await get_by_slug(db, slug, project.account_id)
    if existing is not None and existing.id != project.id:
        raise ProjectError(f"A project with slug '{slug}' already exists.")
    project.name = name
    project.slug = slug
    await db.flush()
    return project


async def archive_project(db: AsyncSession, project: Project) -> Project:
    project.archived_at = datetime.now(timezone.utc)
    await db.flush()
    return project
