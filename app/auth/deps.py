import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import ApiToken, User
from app.auth.service import get_user_by_id, verify_api_token
from app.database import get_db

_bearer = HTTPBearer(auto_error=False)


async def _load_project_module_context(
    request: Request,
    db: AsyncSession,
    auth: User | ApiToken,
) -> None:
    """Resolve capabilities once for this request; never persist them in auth state."""
    if getattr(request.state, "project_modules_loaded", False):
        return
    from app.projects.access import resolve_current_project
    from app.projects.modules import CORE_MODULE, effective_modules, module_states

    request.state.auth_context = auth
    if isinstance(auth, ApiToken):
        project_id = auth.project_id
        request.state.current_project_id = project_id
    else:
        project = await resolve_current_project(db, auth, request)
        project_id = project.id if project else None
    if project_id is not None:
        configured = await module_states(db, project_id)
        request.state.module_states = configured
        request.state.enabled_modules = effective_modules(
            configured, plan_code=getattr(auth, "plan_code", None)
        )
    else:
        request.state.module_states = {}
        request.state.enabled_modules = frozenset({CORE_MODULE})
    request.state.project_modules_loaded = True


async def current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await get_user_by_id(db, uuid.UUID(user_id))
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid session")
    from app.accounts.plans import active_plan_code

    setattr(user, "plan_code", await active_plan_code(db, user.account_id))
    await _load_project_module_context(request, db, user)
    return user


async def current_user_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """UI routes — redirect to login instead of 401."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    user = await get_user_by_id(db, uuid.UUID(user_id))
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    from app.accounts.plans import active_plan_code

    setattr(user, "plan_code", await active_plan_code(db, user.account_id))
    await _load_project_module_context(request, db, user)
    return user


async def require_owner_session(user: User = Depends(current_user)) -> User:
    if user.account_role != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


async def require_superadmin(user: User = Depends(current_user)) -> User:
    if not user.is_superadmin:
        raise HTTPException(status_code=403, detail="Superadmin only")
    return user


async def api_token_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: AsyncSession = Depends(get_db),
) -> ApiToken:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Bearer token required")
    token = await verify_api_token(db, credentials.credentials)
    if token is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return token


async def api_or_session_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: AsyncSession = Depends(get_db),
) -> User | ApiToken:
    """Accept session cookie OR Bearer token."""
    if credentials:
        token = await verify_api_token(db, credentials.credentials)
        if token is None:
            raise HTTPException(status_code=401, detail="Invalid or revoked token")
        await _load_project_module_context(request, db, token)
        return token
    user_id = request.session.get("user_id")
    if user_id:
        user = await get_user_by_id(db, uuid.UUID(user_id))
        if user:
            from app.accounts.plans import active_plan_code

            setattr(user, "plan_code", await active_plan_code(db, user.account_id))
            await _load_project_module_context(request, db, user)
            return user
    raise HTTPException(status_code=401, detail="Not authenticated")


async def require_write(
    auth: User | ApiToken = Depends(api_or_session_user),
) -> User | ApiToken:
    if isinstance(auth, ApiToken) and auth.scopes != "write":
        raise HTTPException(status_code=403, detail="Token is read-only")
    return auth


async def require_owner(
    auth: User | ApiToken = Depends(api_or_session_user),
) -> User:
    if not isinstance(auth, User):
        raise HTTPException(status_code=403, detail="Owner session required (tokens not allowed here)")
    if auth.account_role != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return auth


async def current_project_id(
    request: Request,
    auth: User | ApiToken = Depends(api_or_session_user),
    db: AsyncSession = Depends(get_db),
) -> uuid.UUID:
    """REST dependency: the effective project for this request (token's or session's)."""
    from app.projects.access import resolve_project_id

    return await resolve_project_id(db, auth, request)
