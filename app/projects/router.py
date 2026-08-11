import hashlib
import secrets
import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user_ui, require_owner
from app.auth.models import ApiToken, User
from app.auth.service import revoke_api_token
from app.config import settings
from app.database import get_db
from app.i18n import resolve_lang
from app.i18n import t as _t
from app.projects import service as ps
from app.projects.access import accessible_project_ids, require_project_access, user_role_on_project
from app.templates_config import templates
from app.ui.flash import flash_success

router = APIRouter(tags=["projects"])


def _connect_snippet(project: ps.Project) -> str:
    """How to point an agent at this project.

    The endpoint and header come first because they are all any MCP client over
    HTTP needs; the CLI line below is one client's shortcut, not the contract.
    """
    return (
        f"URL:    {settings.base_url}/mcp\n"
        f"Header: Authorization: Bearer <TOKEN>\n"
        f"\n"
        f"claude mcp add --transport http {project.slug} {settings.base_url}/mcp \\\n"
        f'  --header "Authorization: Bearer <TOKEN>"'
    )


@router.get("/projects", response_class=HTMLResponse)
async def projects_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    ids = await accessible_project_ids(db, user)
    projects = [
        p
        for p in await ps.list_projects(db, user.account_id, include_archived=True)
        if p.id in ids
    ]
    return templates.TemplateResponse(request, "projects_list.html", {"user": user, "projects": projects})


@router.get("/projects/new", response_class=HTMLResponse)
async def projects_new_page(
    request: Request,
    user: User = Depends(require_owner),
):
    return templates.TemplateResponse(request, "projects_new.html", {"user": user, "error": None})


@router.post("/projects/new")
async def projects_new_submit(
    request: Request,
    name: str = Form(...),
    slug: str = Form(""),
    description: str = Form(""),
    color: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    try:
        project = await ps.create_project(
            db,
            name=name,
            account_id=user.account_id,
            slug=slug or None,
            description=description or None,
            color=color or None,
        )
        await db.commit()
    except ps.ProjectError as e:
        return templates.TemplateResponse(
            request, "projects_new.html", {"user": user, "error": str(e)}, status_code=422
        )
    return RedirectResponse(f"/projects/{project.slug}/settings", status_code=303)


@router.get("/projects/{slug}/settings", response_class=HTMLResponse)
async def project_settings(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    project = await ps.get_by_slug(db, slug, user.account_id)
    if project is None:
        return Response(status_code=404, content="Project not found")
    await require_project_access(db, user, project.id)
    can_write = await user_role_on_project(db, user, project.id) != "viewer"
    tokens = list((await db.execute(
        select(ApiToken).where(
            ApiToken.project_id == project.id,
            ApiToken.revoked_at.is_(None),
        ).order_by(ApiToken.created_at.desc())
    )).scalars().all())
    snippet = _connect_snippet(project)
    return templates.TemplateResponse(request, "projects_settings.html", {
        "user": user, "project": project, "tokens": tokens, "can_write": can_write,
        "snippet": snippet, "new_token": request.session.pop("new_token", None),
    })


@router.post("/projects/{slug}/settings")
async def project_settings_update(
    slug: str,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    color: str = Form(""),
    repo_url: str = Form(""),
    github_webhook_secret: str = Form(""),
    sentry_project_slug: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    project = await ps.get_by_slug(db, slug, user.account_id)
    if project is None:
        return Response(status_code=404, content="Project not found")
    new_sentry_slug = sentry_project_slug.strip() or None
    if new_sentry_slug:
        # Único por cuenta: dos proyectos no pueden reclamar el mismo slug de Sentry.
        from app.projects.models import Project
        clash = await db.scalar(select(Project.id).where(
            Project.account_id == user.account_id,
            Project.sentry_project_slug == new_sentry_slug,
            Project.id != project.id,
        ))
        if clash:
            # Re-render the full settings page with the error — a plain-text 422 on a
            # regular form is a dead end that loses everything the user typed.
            tokens = list((await db.execute(
                select(ApiToken).where(
                    ApiToken.project_id == project.id,
                    ApiToken.revoked_at.is_(None),
                ).order_by(ApiToken.created_at.desc())
            )).scalars().all())
            snippet = _connect_snippet(project)
            # Display-only echo of the submitted values; nothing below flushes, so the
            # in-memory mutation is discarded when the session closes without commit.
            project.name = name.strip() or project.name
            project.description = description.strip() or None
            project.repo_url = repo_url.strip() or None
            project.github_webhook_secret = github_webhook_secret.strip() or None
            project.sentry_project_slug = new_sentry_slug
            return templates.TemplateResponse(request, "projects_settings.html", {
                "user": user, "project": project, "tokens": tokens, "can_write": True,
                "snippet": snippet, "new_token": None,
                "error": _t("projects.sentry_slug_taken", resolve_lang(request)),
            }, status_code=422)
    await ps.update_project(db, project, {
        "name": name.strip(),
        "description": description.strip() or None,
        "color": color.strip() or None,
        "repo_url": repo_url.strip() or None,
        "github_webhook_secret": github_webhook_secret.strip() or None,
        "sentry_project_slug": new_sentry_slug,
    })
    await db.commit()
    if request.session.get("current_project_id") == str(project.id):
        request.session["current_project_color"] = project.color or "#6366f1"
    flash_success(request, message=_t("flash.settings_saved", resolve_lang(request)))
    return RedirectResponse(f"/projects/{slug}/settings", status_code=303)


@router.post("/projects/{slug}/tokens")
async def project_token_create(
    slug: str,
    request: Request,
    token_name: str = Form(...),
    scopes: str = Form("write"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    project = await ps.get_by_slug(db, slug, user.account_id)
    if project is None:
        return Response(status_code=404, content="Project not found")
    role = await user_role_on_project(db, user, project.id)
    if role is None:
        return Response(status_code=403, content="No access to this project")
    if scopes not in ("read", "write"):
        scopes = "read"
    # Token scope must not exceed the minter's role on the project.
    if scopes == "write" and role == "viewer":
        return Response(status_code=403, content="Viewer cannot mint a write token")
    raw = secrets.token_urlsafe(32)
    token = ApiToken(
        name=token_name,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        scopes=scopes,
        created_by=user.id,
        project_id=project.id,
    )
    db.add(token)
    await db.commit()
    # ponytail: show raw token once via session flash, cleared in GET /settings
    request.session["new_token"] = raw
    return RedirectResponse(f"/projects/{slug}/settings", status_code=303)


@router.post("/projects/{slug}/tokens/{token_id}/revoke")
async def project_token_revoke(
    slug: str,
    token_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    project = await ps.get_by_slug(db, slug, user.account_id)
    if project is None:
        return Response(status_code=404, content="Project not found")
    await require_project_access(db, user, project.id, need_write=True)
    # Only revoke a token that belongs to this project (no cross-account/project revoke).
    token = await db.get(ApiToken, token_id)
    if token is not None and token.project_id == project.id:
        await revoke_api_token(db, token_id)
    return RedirectResponse(f"/projects/{slug}/settings", status_code=303)


@router.post("/ui/project/switch")
async def switch_project(
    request: Request,
    project_id: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    try:
        pid = uuid.UUID(project_id)
        project = await ps.get_by_id(db, pid, user.account_id)
        ids = await accessible_project_ids(db, user)
        if project and not project.archived_at and project.id in ids:
            request.session["current_project_id"] = str(project.id)
            request.session["current_project_name"] = project.name
            request.session["current_project_slug"] = project.slug
            request.session["current_project_color"] = project.color or "#6366f1"
            flash_success(request, message=_t(
                "flash.project_active", resolve_lang(request), name=project.name))
    except (ValueError, AttributeError):
        pass
    redirect = request.headers.get("referer", "/")
    return RedirectResponse(redirect, status_code=303)
