"""Single chokepoint for per-project access. Every UI/REST path scopes through here.

Owner ⇒ implicit editor on every project of their account. Members get explicit
`project_members` grants (viewer | editor). Cross-account access is impossible: the
project's `account_id` must match the user's.
"""
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.projects.models import Project, ProjectMember


async def accessible_project_ids(db: AsyncSession, user) -> set[uuid.UUID]:
    if user.account_role == "owner":
        rows = await db.execute(select(Project.id).where(Project.account_id == user.account_id))
        return set(rows.scalars().all())
    rows = await db.execute(
        select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
    )
    return set(rows.scalars().all())


async def user_role_on_project(db: AsyncSession, user, project_id: uuid.UUID) -> str | None:
    """Returns 'editor' | 'viewer' | None (no access). Owner ⇒ 'editor' on own-account projects."""
    proj = await db.get(Project, project_id)
    if proj is None or proj.account_id != user.account_id:
        return None
    if user.account_role == "owner":
        return "editor"
    return await db.scalar(
        select(ProjectMember.role).where(
            ProjectMember.user_id == user.id, ProjectMember.project_id == project_id
        )
    )


async def require_project_access(
    db: AsyncSession, user, project_id: uuid.UUID, *, need_write: bool = False
) -> None:
    role = await user_role_on_project(db, user, project_id)
    if role is None:
        raise HTTPException(status_code=403, detail="No access to this project")
    if need_write and role == "viewer":
        raise HTTPException(status_code=403, detail="Viewer cannot write to this project")


async def resolve_project_id(db: AsyncSession, auth, request) -> uuid.UUID:
    """Effective project for a REST/UI request: an ApiToken's project, or the session
    user's selected + accessible project. Raises 400/403 when none applies."""
    from app.auth.models import ApiToken

    cached = getattr(request.state, "current_project_id", None)
    if isinstance(cached, uuid.UUID):
        return cached
    if isinstance(auth, ApiToken):
        if auth.project_id is None:
            raise HTTPException(status_code=400, detail="Token has no project assigned")
        return auth.project_id
    sid = request.session.get("current_project_id")
    if sid:
        try:
            pid = uuid.UUID(str(sid))
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid project") from e
        if pid not in await accessible_project_ids(db, auth):
            raise HTTPException(status_code=403, detail="No access to the selected project")
        return pid
    # No explicit selection → fall back to the user's first accessible project.
    project = await resolve_current_project(db, auth, request)
    if project is None:
        raise HTTPException(status_code=400, detail="No project available — create one first")
    return project.id


async def resolve_current_project(db: AsyncSession, user, request) -> Project | None:
    """UI helper: the session's selected project (if accessible) or the user's earliest
    accessible project, or None when the user can reach no project."""
    if getattr(request.state, "project_context_loaded", False):
        return getattr(request.state, "current_project", None)
    ids = await accessible_project_ids(db, user)
    if not ids:
        request.state.project_context_loaded = True
        request.state.current_project = None
        return None
    sid = request.session.get("current_project_id")
    if sid:
        try:
            pid: uuid.UUID | None = uuid.UUID(str(sid))
        except ValueError:
            pid = None
        if pid is not None and pid in ids:
            project = await db.get(Project, pid)
            request.state.project_context_loaded = True
            request.state.current_project = project
            request.state.current_project_id = project.id if project else None
            return project
    rows = await db.execute(
        select(Project).where(Project.id.in_(ids)).order_by(Project.created_at)
    )
    project = rows.scalars().first()
    request.state.project_context_loaded = True
    request.state.current_project = project
    request.state.current_project_id = project.id if project else None
    return project
