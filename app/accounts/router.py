import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts import members as ms
from app.accounts import service as acs
from app.auth.deps import require_owner, require_superadmin
from app.auth.models import User
from app.config import settings
from app.database import get_db
from app.i18n import resolve_lang
from app.i18n import t as _t
from app.projects import service as ps
from app.templates_config import templates
from app.ui.flash import flash_success
from app.webhooks import connection as sconn

router = APIRouter(tags=["accounts"])


# ---------- Super-admin: account management ----------

@router.get("/admin/accounts", response_class=HTMLResponse)
async def accounts_admin(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_superadmin),
):
    accounts = await acs.list_accounts(db)
    return templates.TemplateResponse(request, "accounts_admin.html", {
        "user": user, "accounts": accounts, "error": None,
        "new_owner": request.session.pop("new_owner", None),
    })


@router.post("/admin/accounts")
async def accounts_create(
    request: Request,
    name: str = Form(...),
    owner_name: str = Form(...),
    owner_email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_superadmin),
):
    try:
        await acs.create_account(db, name, owner_email, owner_name, password)
    except acs.AccountError as e:
        accounts = await acs.list_accounts(db)
        return templates.TemplateResponse(request, "accounts_admin.html", {
            "user": user, "accounts": accounts, "error": str(e), "new_owner": None,
        }, status_code=422)
    request.session["new_owner"] = owner_email
    return RedirectResponse("/admin/accounts", status_code=303)


@router.post("/admin/accounts/{account_id}/active")
async def accounts_toggle(
    account_id: uuid.UUID,
    active: str = Form("true"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_superadmin),
):
    await acs.set_account_active(db, account_id, active == "true")
    return RedirectResponse("/admin/accounts", status_code=303)


# ---------- Owner: team members + per-project grant matrix ----------

@router.get("/account/members", response_class=HTMLResponse)
async def account_members(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    members = await ms.list_members(db, user.account_id)
    projects = await ps.list_projects(db, user.account_id, include_archived=False)
    matrix = await ms.member_matrix(db, user.account_id)
    return templates.TemplateResponse(request, "account_members.html", {
        "user": user, "members": members, "projects": projects, "matrix": matrix,
        "error": None, "new_member": request.session.pop("new_member", None),
    })


@router.post("/account/members")
async def account_member_create(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    try:
        await ms.create_member(db, user.account_id, email, name, password)
    except ms.MemberError as e:
        members = await ms.list_members(db, user.account_id)
        projects = await ps.list_projects(db, user.account_id, include_archived=False)
        matrix = await ms.member_matrix(db, user.account_id)
        return templates.TemplateResponse(request, "account_members.html", {
            "user": user, "members": members, "projects": projects, "matrix": matrix,
            "error": str(e), "new_member": None,
        }, status_code=422)
    request.session["new_member"] = email
    return RedirectResponse("/account/members", status_code=303)


# ---------- Owner: Sentry integration (spec 2026-07-10) ----------

@router.get("/account/integrations", response_class=HTMLResponse)
async def account_integrations(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    conn = await sconn.get_or_create(db, user.account_id)
    await db.commit()
    return templates.TemplateResponse(request, "account_integrations.html", {
        "user": user, "conn": conn,
        "webhook_url": f"{settings.base_url}/webhooks/sentry/{conn.webhook_token}",
        "unmatched": await sconn.count_unmatched(db, user.account_id),
        "reattached": request.session.pop("reattached", None),
    })


@router.post("/account/integrations")
async def account_integrations_save(
    request: Request,
    client_secret: str = Form(""),
    api_token: str = Form(""),
    org_slug: str = Form(""),
    base_url: str = Form(""),
    clear_client_secret: bool = Form(False),
    clear_api_token: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    conn = await sconn.get_or_create(db, user.account_id)
    try:
        await sconn.update_connection(db, conn, client_secret=client_secret,
                                      api_token=api_token, org_slug=org_slug,
                                      base_url=base_url,
                                      clear_client_secret=clear_client_secret,
                                      clear_api_token=clear_api_token)
    except sconn.SentryConfigError as e:
        return HTMLResponse(str(e), status_code=422)
    await db.commit()
    flash_success(request, message=_t("flash.settings_saved", resolve_lang(request)))
    return RedirectResponse("/account/integrations", status_code=303)


@router.post("/account/integrations/regenerate")
async def account_integrations_regenerate(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    conn = await sconn.get_or_create(db, user.account_id)
    await sconn.regenerate_token(db, conn)
    await db.commit()
    return RedirectResponse("/account/integrations", status_code=303)


@router.post("/account/integrations/reattach")
async def account_integrations_reattach(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    n = await sconn.reattach_unmatched(db, user.account_id)
    await db.commit()
    request.session["reattached"] = n
    return RedirectResponse("/account/integrations", status_code=303)


@router.post("/account/members/grant")
async def account_member_grant(
    user_id: uuid.UUID = Form(...),
    project_id: uuid.UUID = Form(...),
    role: str = Form("none"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    await ms.set_grant(db, user.account_id, user_id, project_id, role)
    return RedirectResponse("/account/members", status_code=303)
