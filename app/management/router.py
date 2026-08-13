"""Management (PMO) UI: /management/{documentos,plan,pendientes}.

UI is a viewer + light editor: documents and pendings are editable here AND via MCP;
the Gantt (plan) is read-only here and edited ONLY via MCP (pulsyr_gantt_*). Every write
re-uses the service layer (audit + validation) and the project-access chokepoint.
"""

import re
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.plans import PlanLimitError
from app.auth.deps import current_user_ui
from app.auth.models import User
from app.database import get_db
from app.enums import DELIVERABLE_STATUSES, DELIVERABLE_TYPES, PENDING_STATUSES
from app.i18n import resolve_lang
from app.i18n import t as _t
from app.management import gantt
from app.management import service as mservice
from app.projects.access import resolve_current_project, user_role_on_project
from app.templates_config import templates
from app.ui.flash import flash_success

router = APIRouter(tags=["management"])

_SUBTABS = ("documentos", "plan", "pendientes")


async def _pid(db: AsyncSession, user: User, request: Request) -> uuid.UUID:
    p = await resolve_current_project(db, user, request)
    return p.id if p else uuid.UUID(int=0)


async def _require_write(db: AsyncSession, user: User, project_id: uuid.UUID) -> Optional[Response]:
    role = await user_role_on_project(db, user, project_id)
    if role is None:
        return Response(status_code=404)
    if role == "viewer":
        return Response(content="Viewer cannot modify this project", status_code=403)
    return None


def _refresh_to(path: str) -> Response:
    return RedirectResponse(path, status_code=303)


def _safe_filename(name: str, doc_type: str) -> str:
    base = re.sub(r'[^\w.\- ]', "_", name).strip() or "deliverable"
    return base if base.lower().endswith(f".{doc_type}") else f"{base}.{doc_type}"


# ---------- Screens ----------

@router.get("/management", response_class=HTMLResponse)
async def management_home(request: Request):
    return RedirectResponse("/management/documentos", status_code=302)


@router.get("/management/documentos", response_class=HTMLResponse)
async def documentos_screen(
    request: Request,
    compartment: Optional[str] = None,
    q: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    pid = await _pid(db, user, request)
    compartments = await mservice.list_compartments(db, pid)
    sel_id: Optional[uuid.UUID] = None
    if compartment:
        try:
            sel_id = uuid.UUID(compartment)
        except ValueError:
            sel_id = None
    deliverables = await mservice.list_deliverables(db, pid, compartment_id=sel_id, q=q)
    comp_names = {str(c.id): c.name for c in compartments}
    return templates.TemplateResponse(
        request, "management_documentos.html",
        {"user": user, "subtab": "documentos", "subtabs": _SUBTABS,
         "compartments": compartments, "deliverables": deliverables,
         "comp_names": comp_names, "selected_compartment": compartment or "", "q": q or "",
         "doc_types": DELIVERABLE_TYPES, "doc_statuses": DELIVERABLE_STATUSES},
    )


@router.get("/management/documentos/{deliverable_id}", response_class=HTMLResponse)
async def deliverable_detail(
    deliverable_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    pid = await _pid(db, user, request)
    d = await mservice.get_deliverable(db, pid, deliverable_id)
    if d is None:
        return Response(status_code=404, content="Deliverable not found")
    versions = await mservice.list_versions(db, d.id)
    compartments = await mservice.list_compartments(db, pid)
    comp_names = {str(c.id): c.name for c in compartments}
    # Text preview only for md (rendered escaped — safe, no dependency). html/pdf preview
    # via a sandboxed iframe hitting the inline download endpoint.
    text_preview = None
    if d.doc_type == "md":
        _, v = await mservice.get_version(db, pid, d.id)
        text_preview = v.content.decode("utf-8", errors="replace")
    return templates.TemplateResponse(
        request, "management_deliverable.html",
        {"user": user, "subtab": "documentos", "subtabs": _SUBTABS,
         "d": d, "versions": versions, "comp_names": comp_names, "text_preview": text_preview},
    )


@router.get("/management/pendientes", response_class=HTMLResponse)
async def pendientes_screen(
    request: Request,
    status: Optional[str] = None,
    owner: Optional[str] = None,
    overdue: bool = False,
    group: str = "status",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    pid = await _pid(db, user, request)
    pendings = await mservice.list_pendings(
        db, pid, status=status or None, owner=owner or None, overdue=overdue,
    )
    plan_tasks = await mservice.list_plan_tasks(db, pid)
    groups = _group_pendings(pendings, group)
    return templates.TemplateResponse(
        request, "management_pendientes.html",
        {"user": user, "subtab": "pendientes", "subtabs": _SUBTABS,
         "groups": groups, "plan_tasks": plan_tasks, "today": date.today(),
         "statuses": PENDING_STATUSES,
         "filters": {"status": status or "", "owner": owner or "",
                     "overdue": overdue, "group": group}},
    )


@router.get("/management/plan", response_class=HTMLResponse)
async def plan_screen(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    pid = await _pid(db, user, request)
    tasks = await mservice.list_plan_tasks(db, pid)
    task_dicts = mservice.plan_tasks_to_dicts(tasks)
    start, end = gantt.plan_bounds(task_dicts)
    if start and end:
        axis = gantt.build_axis(start, end)
        rows = gantt.plan_rows(task_dicts, axis["columns"])
        today_frac = gantt.today_fraction(date.today(), axis["columns"])
    else:
        axis = {"columns": [], "groups": [], "n": 0}
        rows = []
        today_frac = None
    return templates.TemplateResponse(
        request, "management_plan.html",
        {"user": user, "subtab": "plan", "subtabs": _SUBTABS,
         "axis": axis, "rows": rows, "today_frac": today_frac, "has_plan": bool(rows)},
    )


def _group_pendings(pendings: list, group: str) -> list[tuple[str, list]]:
    """Return ordered (group_label, items) pairs for the group-by view."""
    if group == "none":
        return [("", pendings)]
    if group == "owner":
        buckets: dict[str, list] = {}
        for p in pendings:
            buckets.setdefault(p.owner or "—", []).append(p)
        return sorted(buckets.items(), key=lambda kv: kv[0].lower())
    if group == "due":
        today = date.today()
        order = ["overdue", "today", "upcoming", "none"]
        buckets = {k: [] for k in order}
        for p in pendings:
            if p.due_date is None:
                buckets["none"].append(p)
            elif p.status != "done" and p.due_date < today:
                buckets["overdue"].append(p)
            elif p.due_date == today:
                buckets["today"].append(p)
            else:
                buckets["upcoming"].append(p)
        return [(k, buckets[k]) for k in order if buckets[k]]
    # default: by status, in lifecycle order
    order = list(PENDING_STATUSES)
    buckets = {k: [] for k in order}
    for p in pendings:
        buckets.setdefault(p.status, []).append(p)
    return [(k, buckets[k]) for k in order if buckets.get(k)]


# ---------- Actions: documentos ----------

@router.post("/ui/management/compartments")
async def ui_create_compartment(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    pid = await _pid(db, user, request)
    guard = await _require_write(db, user, pid)
    if guard is not None:
        return guard
    try:
        await mservice.create_compartment(db, pid, name, description or None, user.email)
    except (mservice.ManagementError, PlanLimitError) as e:
        message = (
            _t(f"plan.limit.{e.resource}", resolve_lang(request), limit=e.limit)
            if isinstance(e, PlanLimitError)
            else str(e)
        )
        return Response(content=message, status_code=422)
    await db.commit()
    flash_success(request, message=_t("management.flash.compartment_created", resolve_lang(request)))
    return _refresh_to("/management/documentos")


@router.post("/ui/management/documentos/upload")
async def ui_upload_deliverable(
    request: Request,
    file: UploadFile = File(...),
    compartment: str = Form(...),
    name: str = Form(""),
    status: str = Form("draft"),
    owner: str = Form(""),
    summary_md: str = Form(""),
    note: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    pid = await _pid(db, user, request)
    guard = await _require_write(db, user, pid)
    if guard is not None:
        return guard
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in DELIVERABLE_TYPES:
        return Response(
            content=f"Unsupported file type '.{ext}'. Allowed: {', '.join(DELIVERABLE_TYPES)}.",
            status_code=422,
        )
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    content = await file.read()
    try:
        await mservice.put_deliverable(
            db, pid, compartment_name=compartment, name=(name.strip() or stem),
            doc_type=ext, content=content, actor=user.email,
            summary_md=summary_md or None, status=status or None, owner=owner or None,
            note=note or None,
        )
    except (mservice.ManagementError, PlanLimitError) as e:
        message = (
            _t(f"plan.limit.{e.resource}", resolve_lang(request), limit=e.limit)
            if isinstance(e, PlanLimitError)
            else str(e)
        )
        return Response(content=message, status_code=422)
    await db.commit()
    flash_success(request, message=_t("management.flash.deliverable_saved", resolve_lang(request)))
    return _refresh_to("/management/documentos")


@router.post("/ui/management/documentos/{deliverable_id}/rollback")
async def ui_rollback_deliverable(
    deliverable_id: uuid.UUID,
    request: Request,
    version_no: int = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    pid = await _pid(db, user, request)
    guard = await _require_write(db, user, pid)
    if guard is not None:
        return guard
    try:
        await mservice.rollback_deliverable(db, pid, deliverable_id, version_no, user.email)
    except (mservice.ManagementError, PlanLimitError) as e:
        message = (
            _t(f"plan.limit.{e.resource}", resolve_lang(request), limit=e.limit)
            if isinstance(e, PlanLimitError)
            else str(e)
        )
        return Response(content=message, status_code=422)
    await db.commit()
    flash_success(request, message=_t("management.flash.rolled_back", resolve_lang(request)))
    return _refresh_to(f"/management/documentos/{deliverable_id}")


@router.get("/management/documentos/{deliverable_id}/download")
async def download_deliverable(
    deliverable_id: uuid.UUID,
    request: Request,
    v: Optional[int] = None,
    disposition: str = "attachment",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    pid = await _pid(db, user, request)
    try:
        d, version = await mservice.get_version(db, pid, deliverable_id, v)
    except mservice.ManagementError:
        return Response(status_code=404, content="Not found")
    disp = "inline" if disposition == "inline" else "attachment"
    fname = _safe_filename(d.name, d.doc_type)
    headers = {
        "Content-Disposition": f'{disp}; filename="{fname}"',
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=version.content, media_type=version.mime, headers=headers)


# ---------- Actions: pendientes ----------

def _parse_date(value: str) -> Optional[date]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@router.post("/ui/management/pendientes")
async def ui_upsert_pending(
    request: Request,
    pending_id: str = Form(""),
    title: str = Form(""),
    detail_md: str = Form(""),
    owner: str = Form(""),
    status: str = Form("open"),
    due_date: str = Form(""),
    plan_task_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    pid = await _pid(db, user, request)
    guard = await _require_write(db, user, pid)
    if guard is not None:
        return guard
    pid_arg = uuid.UUID(pending_id) if pending_id else None
    ptid = uuid.UUID(plan_task_id) if plan_task_id else None
    try:
        await mservice.upsert_pending(
            db, pid, actor=user.email, pending_id=pid_arg, title=title,
            detail_md=detail_md or None, owner=owner or None, status=status,
            due_date=_parse_date(due_date), plan_task_id=ptid,
        )
    except mservice.ManagementError as e:
        return Response(content=str(e), status_code=422)
    await db.commit()
    flash_success(request, message=_t("management.flash.pending_saved", resolve_lang(request)))
    return _refresh_to("/management/pendientes")


@router.post("/ui/management/pendientes/{pending_id}/complete")
async def ui_complete_pending(
    pending_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    pid = await _pid(db, user, request)
    guard = await _require_write(db, user, pid)
    if guard is not None:
        return guard
    try:
        await mservice.complete_pending(db, pid, pending_id, user.email)
    except mservice.ManagementError as e:
        return Response(content=str(e), status_code=422)
    await db.commit()
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@router.post("/ui/management/pendientes/{pending_id}/delete")
async def ui_delete_pending(
    pending_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    pid = await _pid(db, user, request)
    guard = await _require_write(db, user, pid)
    if guard is not None:
        return guard
    try:
        await mservice.delete_pending(db, pid, pending_id, user.email)
    except mservice.ManagementError as e:
        return Response(content=str(e), status_code=422)
    await db.commit()
    return Response(status_code=204, headers={"HX-Refresh": "true"})
