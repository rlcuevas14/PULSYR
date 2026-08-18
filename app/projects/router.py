import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.plans import PlanLimitError
from app.auth.deps import current_user_ui, require_owner
from app.auth.models import ApiToken, User
from app.auth.service import create_api_token, revoke_api_token
from app.config import settings
from app.database import get_db
from app.i18n import resolve_lang
from app.i18n import t as _t
from app.projects import modules as project_modules
from app.projects import service as ps
from app.projects.access import accessible_project_ids, require_project_access, user_role_on_project
from app.secret_fields import resolve_write_only_secret
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


async def _settings_context(
    db: AsyncSession,
    request: Request,
    user: User,
    project: ps.Project,
    *,
    new_token: str | None = None,
    error: str | None = None,
) -> dict:
    role = await user_role_on_project(db, user, project.id)
    tokens = list((await db.execute(
        select(ApiToken).where(
            ApiToken.project_id == project.id,
            ApiToken.revoked_at.is_(None),
        ).order_by(ApiToken.created_at.desc())
    )).scalars().all())
    module_states = (
        request.state.module_states
        if getattr(request.state, "current_project_id", None) == project.id
        and getattr(request.state, "project_modules_loaded", False)
        else await project_modules.module_states(db, project.id)
    )
    from app.webhooks.connection import outbound

    return {
        "user": user,
        "project": project,
        "tokens": tokens,
        "can_write": role != "viewer",
        "snippet": _connect_snippet(project),
        "new_token": new_token,
        "error": error,
        "module_states": module_states,
        "module_preset": project_modules.infer_preset(module_states),
        "module_presets": project_modules.PRESETS,
        "has_sentry_connection": await outbound(db, user.account_id) is not None,
    }


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
    return templates.TemplateResponse(request, "projects_new.html", {
        "user": user,
        "error": None,
        "module_presets": project_modules.PRESETS,
        "selected_preset": "solo",
    })


@router.post("/projects/new")
async def projects_new_submit(
    request: Request,
    name: str = Form(...),
    slug: str = Form(""),
    description: str = Form(""),
    color: str = Form(""),
    preset: str = Form("solo"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    if preset not in project_modules.PRESETS:
        return templates.TemplateResponse(
            request,
            "projects_new.html",
            {
                "user": user,
                "error": _t("modules.invalid_preset", resolve_lang(request)),
                "module_presets": project_modules.PRESETS,
                "selected_preset": "solo",
            },
            status_code=422,
        )
    try:
        project = await ps.create_project(
            db,
            name=name,
            account_id=user.account_id,
            slug=slug or None,
            description=description or None,
            color=color or None,
            preset=preset,
            actor=user.email,
        )
        await db.commit()
    except (ps.ProjectError, PlanLimitError) as e:
        error = (
            _t(f"plan.limit.{e.resource}", resolve_lang(request), limit=e.limit)
            if isinstance(e, PlanLimitError)
            else str(e)
        )
        return templates.TemplateResponse(
            request,
            "projects_new.html",
            {
                "user": user,
                "error": error,
                "module_presets": project_modules.PRESETS,
                "selected_preset": preset,
            },
            status_code=422,
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
    return templates.TemplateResponse(
        request,
        "projects_settings.html",
        await _settings_context(
            db,
            request,
            user,
            project,
            new_token=request.session.pop("new_token", None),
        ),
    )


@router.post("/projects/{slug}/settings")
async def project_settings_update(
    slug: str,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    color: str = Form(""),
    repo_url: str = Form(""),
    github_webhook_secret: str = Form(""),
    clear_github_webhook_secret: bool = Form(False),
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
            # Display-only echo of the submitted values; nothing below flushes, so the
            # in-memory mutation is discarded when the session closes without commit.
            project.name = name.strip() or project.name
            project.description = description.strip() or None
            project.repo_url = repo_url.strip() or None
            project.sentry_project_slug = new_sentry_slug
            return templates.TemplateResponse(
                request,
                "projects_settings.html",
                await _settings_context(
                    db,
                    request,
                    user,
                    project,
                    error=_t("projects.sentry_slug_taken", resolve_lang(request)),
                ),
                status_code=422,
            )
    await ps.update_project(db, project, {
        "name": name.strip(),
        "description": description.strip() or None,
        "color": color.strip() or None,
        "repo_url": repo_url.strip() or None,
        "github_webhook_secret": resolve_write_only_secret(
            project.github_webhook_secret,
            github_webhook_secret,
            clear=clear_github_webhook_secret,
        ),
        "sentry_project_slug": new_sentry_slug,
    })
    await db.commit()
    if request.session.get("current_project_id") == str(project.id):
        request.session["current_project_color"] = project.color or "#6366f1"
    flash_success(request, message=_t("flash.settings_saved", resolve_lang(request)))
    return RedirectResponse(f"/projects/{slug}/settings", status_code=303)


@router.post("/projects/{slug}/settings/modules/preset")
async def project_modules_apply_preset(
    slug: str,
    request: Request,
    preset: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    project = await ps.get_by_slug(db, slug, user.account_id)
    if project is None:
        return Response(status_code=404, content="Project not found")
    if preset not in project_modules.PRESETS:
        return Response(status_code=422, content="Invalid project preset")
    await project_modules.apply_preset(db, project.id, preset, user.email)
    await db.commit()
    flash_success(request, message=_t("modules.preset_saved", resolve_lang(request)))
    return RedirectResponse(f"/projects/{slug}/settings", status_code=303)


@router.post("/projects/{slug}/settings/modules/{module}")
async def project_module_update(
    slug: str,
    module: str,
    request: Request,
    enabled: bool = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    project = await ps.get_by_slug(db, slug, user.account_id)
    if project is None:
        return Response(status_code=404, content="Project not found")
    if module not in project_modules.OPTIONAL_MODULES:
        return Response(status_code=404, content="Unknown project module")
    await project_modules.set_module_enabled(
        db, project.id, module, enabled, user.email, source="manual"
    )
    await db.commit()
    flash_success(request, message=_t("modules.module_saved", resolve_lang(request)))
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
    try:
        _token, raw = await create_api_token(
            db,
            token_name,
            scopes,
            user.id,
            project_id=project.id,
        )
    except PlanLimitError as e:
        return templates.TemplateResponse(
            request,
            "projects_settings.html",
            await _settings_context(
                db,
                request,
                user,
                project,
                error=_t(f"plan.limit.{e.resource}", resolve_lang(request), limit=e.limit),
            ),
            status_code=422,
        )
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
