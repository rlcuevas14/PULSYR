"""Management (PMO) service — shared by UI, REST-less router, and MCP.

Every mutation emits a ManagementEvent (append-only audit). Business errors raise
ManagementError (→ 422 in UI, ToolError/isError in MCP). Content is always bytes here;
callers (MCP base64/text, UI multipart) decode before calling.
"""

import hashlib
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import (
    DELIVERABLE_MAX_BYTES,
    DELIVERABLE_MIME,
    DELIVERABLE_STATUSES,
    DELIVERABLE_TYPES,
    PENDING_STATUSES,
)
from app.management.models import (
    Compartment,
    Deliverable,
    DeliverableVersion,
    ManagementEvent,
    Pending,
    PlanTask,
)

MAX_PLAN_DEPTH = 3


class ManagementError(ValueError):
    pass


def _event(
    db: AsyncSession, project_id: Optional[uuid.UUID], entity_type: str,
    entity_id: uuid.UUID, actor: str, action: str, payload: Optional[dict] = None,
) -> None:
    db.add(ManagementEvent(
        project_id=project_id, entity_type=entity_type, entity_id=entity_id,
        actor=actor, action=action, payload=payload,
    ))


# ---------- Compartments ----------

async def list_compartments(db: AsyncSession, project_id: uuid.UUID) -> list[Compartment]:
    rows = await db.execute(
        select(Compartment).where(Compartment.project_id == project_id)
        .order_by(Compartment.sort_order, Compartment.name)
    )
    return list(rows.scalars().all())


async def resolve_compartment(
    db: AsyncSession, project_id: uuid.UUID, name: str, *, create: bool, actor: str,
) -> Compartment:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ManagementError("Compartment name cannot be empty.")
    comp = (await db.execute(
        select(Compartment).where(
            Compartment.project_id == project_id,
            func.lower(Compartment.name) == cleaned.lower(),
        )
    )).scalar_one_or_none()
    if comp is not None:
        return comp
    if not create:
        raise ManagementError(f"Compartment '{cleaned}' does not exist.")
    comp = Compartment(project_id=project_id, name=cleaned[:120], created_by=actor)
    db.add(comp)
    await db.flush()
    _event(db, project_id, "compartment", comp.id, actor, "created", {"name": comp.name})
    return comp


async def create_compartment(
    db: AsyncSession, project_id: uuid.UUID, name: str, description: Optional[str], actor: str,
) -> Compartment:
    comp = await resolve_compartment(db, project_id, name, create=True, actor=actor)
    if description is not None:
        comp.description = description
        await db.flush()
    return comp


# ---------- Deliverables ----------

async def list_deliverables(
    db: AsyncSession, project_id: uuid.UUID, *,
    compartment_id: Optional[uuid.UUID] = None, status: Optional[str] = None,
    q: Optional[str] = None,
) -> list[Deliverable]:
    query = select(Deliverable).where(Deliverable.project_id == project_id)
    if compartment_id is not None:
        query = query.where(Deliverable.compartment_id == compartment_id)
    if status:
        query = query.where(Deliverable.status == status)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Deliverable.name.ilike(like),
                                Deliverable.summary_md.ilike(like)))
    query = query.order_by(Deliverable.name)
    return list((await db.execute(query)).scalars().all())


async def get_deliverable(
    db: AsyncSession, project_id: uuid.UUID, deliverable_id: uuid.UUID,
) -> Optional[Deliverable]:
    d = await db.get(Deliverable, deliverable_id)
    if d is None or d.project_id != project_id:
        return None
    return d


async def list_versions(
    db: AsyncSession, deliverable_id: uuid.UUID,
) -> list[DeliverableVersion]:
    rows = await db.execute(
        select(DeliverableVersion).where(DeliverableVersion.deliverable_id == deliverable_id)
        .order_by(DeliverableVersion.version_no.desc())
    )
    return list(rows.scalars().all())


async def get_version(
    db: AsyncSession, project_id: uuid.UUID, deliverable_id: uuid.UUID,
    version_no: Optional[int] = None,
) -> tuple[Deliverable, DeliverableVersion]:
    d = await get_deliverable(db, project_id, deliverable_id)
    if d is None:
        raise ManagementError("Deliverable not found in this project.")
    vno = version_no if version_no is not None else d.current_version
    v = (await db.execute(
        select(DeliverableVersion).where(
            DeliverableVersion.deliverable_id == deliverable_id,
            DeliverableVersion.version_no == vno,
        )
    )).scalar_one_or_none()
    if v is None:
        raise ManagementError(f"Version {vno} not found.")
    return d, v


async def put_deliverable(
    db: AsyncSession, project_id: uuid.UUID, *, compartment_name: str, name: str,
    doc_type: str, content: bytes, actor: str, summary_md: Optional[str] = None,
    status: Optional[str] = None, owner: Optional[str] = None, note: Optional[str] = None,
) -> tuple[Deliverable, bool]:
    """Create a deliverable or append a version. Returns (deliverable, created_new_version).
    Dedup: identical bytes to the current version → no-op (returns False)."""
    name = (name or "").strip()
    if not name:
        raise ManagementError("Deliverable name cannot be empty.")
    if doc_type not in DELIVERABLE_TYPES:
        raise ManagementError(
            f"invalid doc_type '{doc_type}'; use one of: {', '.join(DELIVERABLE_TYPES)}."
        )
    if not content:
        raise ManagementError("Deliverable content is empty.")
    if len(content) > DELIVERABLE_MAX_BYTES:
        raise ManagementError(
            f"file too large ({len(content)} bytes); max is {DELIVERABLE_MAX_BYTES} bytes (10 MB)."
        )
    if status is not None and status not in DELIVERABLE_STATUSES:
        raise ManagementError(
            f"invalid status '{status}'; use one of: {', '.join(DELIVERABLE_STATUSES)}."
        )
    comp = await resolve_compartment(db, project_id, compartment_name, create=True, actor=actor)
    digest = hashlib.sha256(content).hexdigest()
    mime = DELIVERABLE_MIME[doc_type]

    d = (await db.execute(
        select(Deliverable).where(
            Deliverable.compartment_id == comp.id,
            func.lower(Deliverable.name) == name.lower(),
        )
    )).scalar_one_or_none()

    if d is None:
        from app.accounts.plans import ensure_storage_capacity
        from app.projects.models import Project

        project = await db.get(Project, project_id)
        if project is None:
            raise ManagementError("Project not found.")
        await ensure_storage_capacity(db, project.account_id, len(content))
        d = Deliverable(
            project_id=project_id, compartment_id=comp.id, name=name[:200], doc_type=doc_type,
            status=status or "draft", owner=owner, summary_md=summary_md, current_version=1,
            created_by=actor,
        )
        db.add(d)
        await db.flush()
        db.add(DeliverableVersion(
            deliverable_id=d.id, version_no=1, content=content, mime=mime,
            size_bytes=len(content), sha256=digest, note=note, created_by=actor,
        ))
        await db.flush()
        _event(db, project_id, "deliverable", d.id, actor, "created",
               {"name": d.name, "compartment": comp.name, "version": 1})
        return d, True

    # Existing deliverable: apply metadata, then version the content if it changed.
    if status is not None:
        d.status = status
    if owner is not None:
        d.owner = owner
    if summary_md is not None:
        d.summary_md = summary_md
    if d.doc_type != doc_type:
        d.doc_type = doc_type  # type can change if the author re-exports in another format

    current = (await db.execute(
        select(DeliverableVersion).where(
            DeliverableVersion.deliverable_id == d.id,
            DeliverableVersion.version_no == d.current_version,
        )
    )).scalar_one_or_none()
    if current is not None and current.sha256 == digest:
        await db.flush()
        _event(db, project_id, "deliverable", d.id, actor, "updated", {"dedup": True})
        return d, False

    from app.accounts.plans import ensure_storage_capacity
    from app.projects.models import Project

    project = await db.get(Project, project_id)
    if project is None:
        raise ManagementError("Project not found.")
    await ensure_storage_capacity(db, project.account_id, len(content))
    new_no = d.current_version + 1
    db.add(DeliverableVersion(
        deliverable_id=d.id, version_no=new_no, content=content, mime=mime,
        size_bytes=len(content), sha256=digest, note=note, created_by=actor,
    ))
    d.current_version = new_no
    await db.flush()
    _event(db, project_id, "deliverable", d.id, actor, "version_added", {"version": new_no})
    return d, True


async def rollback_deliverable(
    db: AsyncSession, project_id: uuid.UUID, deliverable_id: uuid.UUID,
    to_version: int, actor: str,
) -> Deliverable:
    d, src = await get_version(db, project_id, deliverable_id, to_version)
    from app.accounts.plans import ensure_storage_capacity
    from app.projects.models import Project

    project = await db.get(Project, project_id)
    if project is None:
        raise ManagementError("Project not found.")
    await ensure_storage_capacity(db, project.account_id, src.size_bytes)
    new_no = d.current_version + 1
    db.add(DeliverableVersion(
        deliverable_id=d.id, version_no=new_no, content=src.content, mime=src.mime,
        size_bytes=src.size_bytes, sha256=src.sha256, created_by=actor,
        note=f"rollback to v{to_version}",
    ))
    d.current_version = new_no
    await db.flush()
    _event(db, project_id, "deliverable", d.id, actor, "version_added",
           {"version": new_no, "rollback_of": to_version})
    return d


# ---------- Pendings ----------

async def list_pendings(
    db: AsyncSession, project_id: uuid.UUID, *, status: Optional[str] = None,
    owner: Optional[str] = None, overdue: bool = False,
    plan_task_id: Optional[uuid.UUID] = None,
) -> list[Pending]:
    query = select(Pending).where(Pending.project_id == project_id)
    if status:
        query = query.where(Pending.status == status)
    if owner:
        query = query.where(func.lower(Pending.owner) == owner.strip().lower())
    if overdue:
        query = query.where(
            Pending.due_date < date.today(), Pending.status != "done"
        )
    if plan_task_id is not None:
        query = query.where(Pending.plan_task_id == plan_task_id)
    # Open first, then by due date (nulls last), then newest.
    query = query.order_by(
        (Pending.status == "done"),
        Pending.due_date.asc().nulls_last(),
        Pending.created_at.desc(),
    )
    return list((await db.execute(query)).scalars().all())


async def get_pending(
    db: AsyncSession, project_id: uuid.UUID, pending_id: uuid.UUID,
) -> Optional[Pending]:
    p = await db.get(Pending, pending_id)
    if p is None or p.project_id != project_id:
        return None
    return p


async def upsert_pending(
    db: AsyncSession, project_id: uuid.UUID, *, actor: str,
    pending_id: Optional[uuid.UUID] = None, title: Optional[str] = None,
    detail_md: Optional[str] = None, owner: Optional[str] = None,
    status: Optional[str] = None, due_date: Optional[date] = None,
    plan_task_id: Optional[uuid.UUID] = None,
) -> Pending:
    if status is not None and status not in PENDING_STATUSES:
        raise ManagementError(
            f"invalid status '{status}'; use one of: {', '.join(PENDING_STATUSES)}."
        )
    if plan_task_id is not None:
        pt = await db.get(PlanTask, plan_task_id)
        if pt is None or pt.project_id != project_id:
            raise ManagementError("plan_task_id not found in this project.")

    if pending_id is not None:
        p = await get_pending(db, project_id, pending_id)
        if p is None:
            raise ManagementError("Pending not found in this project.")
        if title is not None:
            p.title = title.strip()
        if detail_md is not None:
            p.detail_md = detail_md
        if owner is not None:
            p.owner = owner
        if due_date is not None:
            p.due_date = due_date
        if plan_task_id is not None:
            p.plan_task_id = plan_task_id
        if status is not None:
            _apply_pending_status(p, status)
        await db.flush()
        _event(db, project_id, "pending", p.id, actor, "updated")
        return p

    clean_title = (title or "").strip()
    if not clean_title:
        raise ManagementError("title cannot be empty.")
    p = Pending(
        project_id=project_id, title=clean_title, detail_md=detail_md, owner=owner,
        status=status or "open", due_date=due_date, plan_task_id=plan_task_id, created_by=actor,
    )
    if p.status == "done":
        p.closed_at = datetime.now(timezone.utc)
    db.add(p)
    await db.flush()
    _event(db, project_id, "pending", p.id, actor, "created", {"title": p.title})
    return p


def _apply_pending_status(p: Pending, status: str) -> None:
    p.status = status
    p.closed_at = datetime.now(timezone.utc) if status == "done" else None


async def complete_pending(
    db: AsyncSession, project_id: uuid.UUID, pending_id: uuid.UUID, actor: str,
) -> Pending:
    p = await get_pending(db, project_id, pending_id)
    if p is None:
        raise ManagementError("Pending not found in this project.")
    _apply_pending_status(p, "done")
    await db.flush()
    _event(db, project_id, "pending", p.id, actor, "completed")
    return p


async def delete_pending(
    db: AsyncSession, project_id: uuid.UUID, pending_id: uuid.UUID, actor: str,
) -> None:
    p = await get_pending(db, project_id, pending_id)
    if p is None:
        raise ManagementError("Pending not found in this project.")
    await db.delete(p)
    _event(db, project_id, "pending", pending_id, actor, "removed")
    await db.flush()


# ---------- Plan tasks (Gantt) ----------

async def list_plan_tasks(db: AsyncSession, project_id: uuid.UUID) -> list[PlanTask]:
    rows = await db.execute(
        select(PlanTask).where(PlanTask.project_id == project_id)
        .order_by(PlanTask.sort_order, PlanTask.name)
    )
    return list(rows.scalars().all())


def plan_tasks_to_dicts(tasks: list[PlanTask]) -> list[dict[str, Any]]:
    """Shape PlanTask rows for the pure gantt geometry layer."""
    return [{
        "id": str(t.id),
        "parent_id": str(t.parent_id) if t.parent_id else None,
        "name": t.name,
        "start": t.start_date,
        "end": t.end_date,
        "progress": t.progress,
        "is_milestone": t.is_milestone,
        "deps": t.deps,
        "sort_order": t.sort_order,
    } for t in tasks]


async def _chain_up(db: AsyncSession, task_id: uuid.UUID) -> list[uuid.UUID]:
    """[task_id, parent, grandparent, ...] up to a root. Guards against corrupt cycles."""
    chain: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    cur: Optional[uuid.UUID] = task_id
    while cur is not None and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        t = await db.get(PlanTask, cur)
        cur = t.parent_id if t is not None else None
    return chain


async def upsert_plan_task(
    db: AsyncSession, project_id: uuid.UUID, *, actor: str,
    task_id: Optional[uuid.UUID] = None, name: Optional[str] = None,
    parent_id: Optional[uuid.UUID] = None, start_date: Optional[date] = None,
    end_date: Optional[date] = None, progress: Optional[int] = None,
    is_milestone: Optional[bool] = None, deps: Optional[list] = None,
    sort_order: Optional[int] = None,
) -> PlanTask:
    if progress is not None and not (0 <= int(progress) <= 100):
        raise ManagementError("progress must be an integer 0-100.")
    if start_date and end_date and end_date < start_date:
        raise ManagementError("end_date cannot be before start_date.")

    if parent_id is not None:
        parent = await db.get(PlanTask, parent_id)
        if parent is None or parent.project_id != project_id:
            raise ManagementError("parent_id not found in this project.")
        # Depth: new task's level = parent depth + 1; cap at MAX_PLAN_DEPTH.
        parent_chain = await _chain_up(db, parent_id)
        if len(parent_chain) + 1 > MAX_PLAN_DEPTH:
            raise ManagementError(
                f"max hierarchy depth is {MAX_PLAN_DEPTH} levels (phase/sub-phase/task)."
            )
        # Cycle: editing a task cannot reparent it under itself or a descendant.
        if task_id is not None and task_id in parent_chain:
            raise ManagementError("a task cannot be its own ancestor (cycle).")

    if task_id is not None:
        t = await db.get(PlanTask, task_id)
        if t is None or t.project_id != project_id:
            raise ManagementError("plan task not found in this project.")
        if name is not None:
            t.name = name.strip()
        if parent_id is not None:
            t.parent_id = parent_id
        if start_date is not None:
            t.start_date = start_date
        if end_date is not None:
            t.end_date = end_date
        if progress is not None:
            t.progress = int(progress)
        if is_milestone is not None:
            t.is_milestone = bool(is_milestone)
        if deps is not None:
            t.deps = deps
        if sort_order is not None:
            t.sort_order = int(sort_order)
        await db.flush()
        _event(db, project_id, "plan_task", t.id, actor, "updated")
        return t

    clean_name = (name or "").strip()
    if not clean_name:
        raise ManagementError("name cannot be empty.")
    t = PlanTask(
        project_id=project_id, name=clean_name, parent_id=parent_id,
        start_date=start_date, end_date=end_date, progress=int(progress or 0),
        is_milestone=bool(is_milestone), deps=deps, sort_order=int(sort_order or 0),
        created_by=actor,
    )
    db.add(t)
    await db.flush()
    _event(db, project_id, "plan_task", t.id, actor, "created", {"name": t.name})
    return t


async def remove_plan_task(
    db: AsyncSession, project_id: uuid.UUID, task_id: uuid.UUID, actor: str,
) -> None:
    t = await db.get(PlanTask, task_id)
    if t is None or t.project_id != project_id:
        raise ManagementError("plan task not found in this project.")
    await db.delete(t)  # children cascade (FK ON DELETE CASCADE)
    _event(db, project_id, "plan_task", task_id, actor, "removed", {"name": t.name})
    await db.flush()
