import hashlib
import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import oauth
from app.auth.deps import current_user_ui
from app.auth.models import ApiToken, User
from app.auth.rate_limit import limit_client, limit_value
from app.auth.service import (
    authenticate,
    create_api_token,
    email_is_registered,
    get_user_by_email,
    get_user_by_oauth_identity,
    hash_password,
    link_oauth_identity,
    oauth_identity_is_linked,
    verify_password,
)
from app.config import settings
from app.database import get_db
from app.i18n import resolve_lang
from app.i18n import t as _t
from app.templates_config import templates
from app.ui.flash import flash_success

# Sign-in lives at the root: /login is a URL people type and the landing links to.
# The old /auth/* paths redirect (compat_router), same as the Spanish route slugs did.
router = APIRouter(tags=["auth"])
compat_router = APIRouter(prefix="/auth", include_in_schema=False)
setup_router = APIRouter(tags=["setup"])
logger = logging.getLogger("pulsyr.auth")


async def _no_users(db: AsyncSession) -> bool:
    count = await db.scalar(select(func.count()).select_from(User))
    return (count or 0) == 0


def _login_context(error: str | None = None) -> dict[str, object]:
    return {
        "error": error,
        "providers": oauth.configured(),
        "public_signup": settings.public_signup,
        "signup_mode": False,
    }


async def _start_session(request: Request, db: AsyncSession, user: User) -> None:
    """Log the user in and seed the active project.

    Seeding matters for the first screen: without it the header pill reads
    "Select project" over real data.
    """
    request.session["user_id"] = str(user.id)
    from app.projects.access import resolve_current_project

    project = await resolve_current_project(db, user, request)
    if project is not None:
        request.session["current_project_id"] = str(project.id)
        request.session["current_project_name"] = project.name
        request.session["current_project_slug"] = project.slug
        request.session["current_project_color"] = project.color or "#6366f1"


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=303)
    if await _no_users(db):
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(request, "login.html", _login_context())


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await authenticate(db, email, password)
    if user is None:
        ip_decision = await limit_client(
            request,
            action="password_login_ip",
            limit=settings.password_ip_rate_limit_attempts,
            window_seconds=settings.oauth_rate_limit_window_seconds,
        )
        identity_decision = await limit_value(
            action="password_login_identity",
            value=email,
            limit=settings.password_rate_limit_attempts,
            window_seconds=settings.oauth_rate_limit_window_seconds,
        )
        if not ip_decision.allowed or not identity_decision.allowed:
            retry_after = max(ip_decision.retry_after, identity_decision.retry_after)
            logger.warning("password_login_rate_limited")
            return templates.TemplateResponse(
                request,
                "login.html",
                _login_context(_t("login.error_rate_limit", resolve_lang(request))),
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
        return templates.TemplateResponse(
            request,
            "login.html",
            _login_context(_t("login.error_credentials", resolve_lang(request))),
            status_code=401,
        )
    await _start_session(request, db, user)
    return RedirectResponse("/", status_code=303)


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request, db: AsyncSession = Depends(get_db)):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=303)
    if await _no_users(db):
        return RedirectResponse("/setup", status_code=303)
    # Closed instance, or open but with no provider configured: there is nothing to
    # offer here, so send them to the form they can actually use.
    if not settings.public_signup or not oauth.configured():
        return RedirectResponse("/login", status_code=303)
    context = _login_context()
    context["signup_mode"] = True
    return templates.TemplateResponse(request, "signup.html", context)


@router.post("/signup", response_class=HTMLResponse)
async def signup_start(
    request: Request,
    provider: str = Form(...),
    accept_terms: bool = Form(False),
):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=303)
    p = oauth.get(provider)
    if not settings.public_signup or p is None:
        return RedirectResponse("/login", status_code=303)
    decision = await limit_client(
        request,
        action="oauth_signup",
        limit=settings.oauth_rate_limit_attempts,
        window_seconds=settings.oauth_rate_limit_window_seconds,
    )
    if not decision.allowed:
        logger.warning("oauth_rate_limited action=signup")
        context = _login_context(_t("login.error_rate_limit", resolve_lang(request)))
        context["signup_mode"] = True
        return templates.TemplateResponse(
            request,
            "signup.html",
            context,
            status_code=429,
            headers={"Retry-After": str(decision.retry_after)},
        )
    if not accept_terms:
        context = _login_context(_t("signup.error_terms", resolve_lang(request)))
        context["signup_mode"] = True
        return templates.TemplateResponse(request, "signup.html", context, status_code=422)
    state = oauth.make_state(
        provider,
        intent="signup",
        terms_version=settings.terms_version,
    )
    return RedirectResponse(oauth.authorize_url(p, state), status_code=303)


# ---------- OAuth ----------

@router.get("/login/{provider}", include_in_schema=False)
async def oauth_start(provider: str, request: Request):
    p = oauth.get(provider)
    if p is None:
        return RedirectResponse("/login", status_code=303)
    decision = await limit_client(
        request,
        action="oauth_login",
        limit=settings.oauth_rate_limit_attempts,
        window_seconds=settings.oauth_rate_limit_window_seconds,
    )
    if not decision.allowed:
        logger.warning("oauth_rate_limited action=login")
        return templates.TemplateResponse(
            request,
            "login.html",
            _login_context(_t("login.error_rate_limit", resolve_lang(request))),
            status_code=429,
            headers={"Retry-After": str(decision.retry_after)},
        )
    return RedirectResponse(
        oauth.authorize_url(p, oauth.make_state(provider, intent="login")),
        status_code=303,
    )


@router.get("/callback/{provider}", include_in_schema=False)
async def oauth_callback(
    provider: str,
    request: Request,
    code: str = "",
    state: str = "",
    db: AsyncSession = Depends(get_db),
):
    lang = resolve_lang(request)
    decision = await limit_client(
        request,
        action="oauth_callback",
        limit=settings.oauth_rate_limit_attempts,
        window_seconds=settings.oauth_rate_limit_window_seconds,
    )
    if not decision.allowed:
        logger.warning("oauth_rate_limited action=callback")
        return templates.TemplateResponse(
            request,
            "login.html",
            _login_context(_t("login.error_rate_limit", lang)),
            status_code=429,
            headers={"Retry-After": str(decision.retry_after)},
        )
    p = oauth.get(provider)
    state_data = oauth.read_state(state, provider)
    if p is None or not code or state_data is None:
        return templates.TemplateResponse(
            request, "login.html", _login_context(_t("login.error_oauth", lang)), status_code=400
        )

    identity = await oauth.fetch_identity(p, code)
    if identity is None:
        return templates.TemplateResponse(
            request, "login.html", _login_context(_t("login.error_oauth", lang)), status_code=400
        )

    user = await get_user_by_oauth_identity(db, provider, identity.subject)
    new_signup = False
    if user is None:
        if await oauth_identity_is_linked(db, provider, identity.subject):
            return templates.TemplateResponse(
                request,
                "login.html",
                _login_context(_t("login.error_account_inactive", lang)),
                status_code=403,
            )
        user = await get_user_by_email(db, identity.email)
        if user is None:
            if await email_is_registered(db, identity.email):
                return templates.TemplateResponse(
                    request,
                    "login.html",
                    _login_context(_t("login.error_account_inactive", lang)),
                    status_code=403,
                )
            is_signup = (
                state_data.get("i") == "signup"
                and state_data.get("v") == settings.terms_version
            )
            if not settings.public_signup or not is_signup:
                error_key = (
                    "login.error_signup_required"
                    if settings.public_signup
                    else "login.error_signup_closed"
                )
                return templates.TemplateResponse(
                    request,
                    "login.html",
                    _login_context(_t(error_key, lang)),
                    status_code=403,
                )
            from app.accounts.plans import FREE
            from app.accounts.service import create_account
            from app.projects.service import create_project

            # Account + owner + Free subscription + starter project + provider
            # identity are committed together: no half-created tenant can escape.
            _, user = await create_account(
                db,
                name=identity.name or identity.email,
                owner_email=identity.email,
                owner_name=identity.name or identity.email,
                password=None,
                plan_code=FREE,
            )
            await create_project(db, name="Default", account_id=user.account_id)
            user.terms_accepted_at = datetime.now(timezone.utc)
            user.terms_version = settings.terms_version
            new_signup = True
        try:
            await link_oauth_identity(
                db,
                user,
                provider=provider,
                subject=identity.subject,
                email=identity.email,
            )
            await db.commit()
        except IntegrityError:
            # A double callback can race after both requests saw no row. Roll back
            # the loser and resolve the winner instead of leaking a 500.
            await db.rollback()
            user = await get_user_by_oauth_identity(db, provider, identity.subject)
            if user is None:
                return templates.TemplateResponse(
                    request,
                    "login.html",
                    _login_context(_t("login.error_oauth", lang)),
                    status_code=409,
                )
    else:
        await link_oauth_identity(
            db,
            user,
            provider=provider,
            subject=identity.subject,
            email=identity.email,
        )
        await db.commit()

    await _start_session(request, db, user)
    from app.accounts.plans import FREE, subscription_for

    subscription = await subscription_for(db, user.account_id)
    needs_onboarding = (
        subscription is not None
        and subscription.plan_code == FREE
        and subscription.onboarding_completed_at is None
    )
    logger.info(
        "oauth_authenticated provider=%s new_signup=%s onboarding=%s",
        provider,
        new_signup,
        needs_onboarding,
    )
    return RedirectResponse("/welcome" if needs_onboarding else "/", status_code=303)


@router.get("/welcome", response_class=HTMLResponse)
async def welcome_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.accounts.plans import FREE, subscription_for
    from app.projects.service import list_projects

    subscription = await subscription_for(db, user.account_id)
    if (
        subscription is None
        or subscription.plan_code != FREE
        or subscription.onboarding_completed_at is not None
    ):
        return RedirectResponse("/", status_code=303)
    projects = await list_projects(db, user.account_id)
    if not projects:
        return RedirectResponse("/projects/new", status_code=303)
    return templates.TemplateResponse(request, "welcome.html", {
        "user": user,
        "project": projects[0],
        "error": None,
        "raw_token": None,
    })


@router.post("/welcome", response_class=HTMLResponse)
async def welcome_submit(
    request: Request,
    project_name: str = Form(...),
    token_name: str = Form("my-agent"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.accounts.plans import FREE, PlanLimitError, subscription_for
    from app.projects import service as projects

    subscription = await subscription_for(db, user.account_id)
    if (
        subscription is None
        or subscription.plan_code != FREE
        or subscription.onboarding_completed_at is not None
    ):
        return RedirectResponse("/", status_code=303)
    available_projects = await projects.list_projects(db, user.account_id)
    if not available_projects:
        return RedirectResponse("/projects/new", status_code=303)
    current = available_projects[0]
    try:
        await projects.rename_project(db, current, project_name)
        _token, raw = await create_api_token(
            db,
            token_name.strip() or "my-agent",
            "write",
            user.id,
            project_id=current.id,
            commit=False,
        )
        subscription.onboarding_completed_at = datetime.now(timezone.utc)
        await db.commit()
    except (projects.ProjectError, PlanLimitError, ValueError) as exc:
        await db.rollback()
        error = (
            _t(f"plan.limit.{exc.resource}", resolve_lang(request), limit=exc.limit)
            if isinstance(exc, PlanLimitError)
            else str(exc)
        )
        return templates.TemplateResponse(request, "welcome.html", {
            "user": user,
            "project": current,
            "error": error,
            "raw_token": None,
        }, status_code=422)
    await _start_session(request, db, user)
    logger.info("free_onboarding_completed")
    return templates.TemplateResponse(request, "welcome.html", {
        "user": user,
        "project": current,
        "error": None,
        "raw_token": raw,
    })


# ---------- Password ----------

@router.get("/password", response_class=HTMLResponse)
async def password_page(request: Request, user: User = Depends(current_user_ui)):
    return templates.TemplateResponse(request, "account_password.html", {"user": user, "error": None})


@router.post("/password")
async def password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    lang = resolve_lang(request)
    error = None
    if user.password_hash is None:
        # OAuth-only user: there is no current password to check, and setting one
        # here would create a second way into the account nobody asked for.
        error = _t("account.password_oauth_only", lang)
    elif not verify_password(current_password, user.password_hash):
        error = _t("account.password_wrong_current", lang)
    elif len(new_password) < 8:
        error = _t("setup.error_password_length", lang)
    elif new_password != confirm_password:
        error = _t("account.password_mismatch", lang)
    if error:
        return templates.TemplateResponse(
            request, "account_password.html", {"user": user, "error": error}, status_code=422
        )
    user.password_hash = hash_password(new_password)
    await db.commit()
    flash_success(request, message=_t("flash.password_changed", lang))
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------- Compat: the pre-2026-08 /auth/* paths ----------
# 301 for the GETs (bookmarks), 308 for the POSTs so the method and body survive.

@compat_router.get("/login")
async def compat_login_page():
    return RedirectResponse("/login", status_code=301)


@compat_router.post("/login")
async def compat_login_submit():
    return RedirectResponse("/login", status_code=308)


@compat_router.get("/password")
async def compat_password_page():
    return RedirectResponse("/password", status_code=301)


@compat_router.post("/password")
async def compat_password_submit():
    return RedirectResponse("/password", status_code=308)


@compat_router.post("/logout")
async def compat_logout():
    return RedirectResponse("/logout", status_code=308)


# ---------- First-run setup ----------

@setup_router.get("/setup", response_class=HTMLResponse, include_in_schema=False)
async def setup_page(request: Request, db: AsyncSession = Depends(get_db)):
    if not await _no_users(db):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "setup.html", {"error": None})


@setup_router.post("/setup", include_in_schema=False)
async def setup_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    project_name: str = Form(...),
    color: str = Form(""),
    preset: str = Form("solo"),
    db: AsyncSession = Depends(get_db),
):
    if not await _no_users(db):
        return RedirectResponse("/", status_code=303)
    if len(password) < 8:
        return templates.TemplateResponse(
            request, "setup.html",
            {"error": _t("setup.error_password_length", resolve_lang(request))},
            status_code=422,
        )
    from app.projects.modules import PRESETS

    if preset not in PRESETS:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"error": _t("modules.invalid_preset", resolve_lang(request))},
            status_code=422,
        )
    from app.accounts.service import create_account
    acc, user = await create_account(
        db, name=name, owner_email=email, owner_name=name, password=password, is_superadmin=True
    )
    request.session["user_id"] = str(user.id)

    # Create the first project and a write token in the same transaction.
    from app.projects.service import create_project
    project = await create_project(
        db,
        name=project_name,
        account_id=acc.id,
        color=color or None,
        preset=preset,
        actor=user.email,
    )
    raw = secrets.token_urlsafe(32)
    token = ApiToken(
        name="my-agent",
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        scopes="write",
        created_by=user.id,
        project_id=project.id,
    )
    db.add(token)
    await db.commit()

    # Set project in session and flash token once
    request.session["current_project_id"] = str(project.id)
    request.session["current_project_name"] = project.name
    request.session["current_project_slug"] = project.slug
    request.session["current_project_color"] = project.color or "#6366f1"
    request.session["new_token"] = raw
    return RedirectResponse(f"/projects/{project.slug}/settings", status_code=303)
