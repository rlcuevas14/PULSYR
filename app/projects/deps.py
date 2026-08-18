"""Transport adapters for project-module guards."""

from collections.abc import Awaitable, Callable

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import api_or_session_user, current_user_ui
from app.auth.models import ApiToken, User
from app.database import get_db
from app.projects.access import resolve_project_id
from app.projects.modules import OPTIONAL_MODULES, ModuleDisabled, require_module


async def _enforce(
    request: Request,
    db: AsyncSession,
    auth: User | ApiToken,
    module: str,
    *,
    ui: bool,
) -> None:
    project_id = await resolve_project_id(db, auth, request)
    if getattr(request.state, "project_modules_loaded", False):
        if module not in request.state.enabled_modules:
            raise ModuleDisabled(project_id, module, ui=ui)
        return
    await require_module(db, project_id, module, ui=ui)


def _validate(module: str) -> str:
    if module not in OPTIONAL_MODULES:
        raise ValueError(f"Unknown optional module: {module}")
    return module


def require_project_module_ui(module: str) -> Callable[..., Awaitable[None]]:
    selected = _validate(module)

    async def dependency(
        request: Request,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(current_user_ui),
    ) -> None:
        await _enforce(request, db, user, selected, ui=True)

    return dependency


def require_project_module_rest(module: str) -> Callable[..., Awaitable[None]]:
    selected = _validate(module)

    async def dependency(
        request: Request,
        db: AsyncSession = Depends(get_db),
        auth: User | ApiToken = Depends(api_or_session_user),
    ) -> None:
        await _enforce(request, db, auth, selected, ui=False)

    return dependency
