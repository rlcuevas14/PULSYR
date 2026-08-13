"""Owner-side team management: collaborators + per-project grants (the matrix)."""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.auth.service import create_user
from app.projects.models import Project, ProjectMember


class MemberError(Exception):
    """Raised on invalid collaborator creation input."""


async def create_member(
    db: AsyncSession, account_id: uuid.UUID, email: str, name: str, password: str
) -> User:
    from app.accounts.plans import ensure_member_capacity

    await ensure_member_capacity(db, account_id)
    email = email.strip().casefold()
    if not email:
        raise MemberError("Email cannot be empty.")
    if len(password) < 8:
        raise MemberError("Password must be at least 8 characters.")
    if await db.scalar(select(User.id).where(func.lower(User.email) == email)):
        raise MemberError("A user with that email already exists.")
    return await create_user(
        db,
        email=email,
        name=name,
        password=password,
        account_id=account_id,
        account_role="member",
    )


async def list_members(db: AsyncSession, account_id: uuid.UUID) -> list[User]:
    rows = await db.execute(
        select(User)
        .where(User.account_id == account_id, User.account_role == "member")
        .order_by(User.created_at)
    )
    return list(rows.scalars().all())


async def set_grant(
    db: AsyncSession,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    role: str,
) -> None:
    """Upsert (viewer|editor) or remove (any other role, e.g. 'none') a project grant.

    Silently ignores cross-account targets — both the user and the project must belong
    to ``account_id``.
    """
    user = await db.get(User, user_id)
    project = await db.get(Project, project_id)
    if (
        user is None
        or project is None
        or user.account_id != account_id
        or project.account_id != account_id
    ):
        return
    existing = await db.scalar(
        select(ProjectMember).where(
            ProjectMember.user_id == user_id, ProjectMember.project_id == project_id
        )
    )
    if role in ("viewer", "editor"):
        if existing is not None:
            existing.role = role
        else:
            db.add(ProjectMember(user_id=user_id, project_id=project_id, role=role))
    elif existing is not None:
        await db.delete(existing)
    await db.commit()


async def member_matrix(db: AsyncSession, account_id: uuid.UUID) -> dict:
    """{user_id: {project_id: role}} for all grants in the account."""
    rows = await db.execute(
        select(ProjectMember.user_id, ProjectMember.project_id, ProjectMember.role)
        .join(User, User.id == ProjectMember.user_id)
        .where(User.account_id == account_id)
    )
    matrix: dict = {}
    for uid, pid, role in rows.all():
        matrix.setdefault(uid, {})[pid] = role
    return matrix
