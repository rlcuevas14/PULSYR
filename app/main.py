import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.templates_config import templates  # noqa: F401 — re-exported for legacy imports

# OBS-1/OBS-2 — Logging centralizado.
# Configuramos el logging raíz una sola vez aquí (al importar el módulo de arranque) para
# que CUALQUIER otro módulo pueda emitir logs sin reconfigurar nada:
#
#     import logging
#     logger = logging.getLogger("pulsyr.<modulo>")   # p. ej. "pulsyr.items", "pulsyr.mcp"
#     logger.info("..."); logger.warning("..."); logger.exception("...")
#
# basicConfig es idempotente (no hace nada si el root ya tiene handlers), así que reimportar
# este módulo no duplica salida. El nivel sube a DEBUG cuando settings.debug está activo.
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("pulsyr")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Pulsyr starting (debug=%s)", settings.debug)
    from app.jobs.worker import worker_loop
    task = asyncio.create_task(worker_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("Pulsyr stopped")


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Red de último recurso para rutas REST/UI (OBS + manejo de errores).

    Loguea la excepción con stack trace (incluye método + path) y devuelve un 500
    genérico sin filtrar detalles internos al cliente.

    NOTA: este handler NO interfiere con el endpoint MCP — el endpoint /mcp atrapa sus
    propios errores y responde 200 con isError ANTES de que la excepción escape, así que
    nunca llega aquí. Tampoco captura HTTPException ni RequestValidationError: esos van
    registrados aparte para conservar su comportamiento normal (status + detail correctos).
    """
    logger.exception("Excepción no manejada en %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "error interno"})


def create_app() -> FastAPI:
    from app.accounts import models as _accounts_models  # noqa: F401 — register ORM in Base.metadata
    from app.accounts.router import router as accounts_router
    from app.auth.router import router as auth_router
    from app.auth.router import setup_router
    from app.items.router import router as items_router
    from app.management import models as _management_models  # noqa: F401 — register ORM in Base.metadata
    from app.management.router import router as management_router
    from app.projects import models as _projects_models  # noqa: F401 — register ORM in Base.metadata
    from app.projects.router import router as projects_router
    from app.scopes.router import router as scopes_router
    from app.threads import models as _threads_models  # noqa: F401 — register ORM in Base.metadata
    from app.threads.router import router as threads_router
    from app.ui.router import router as ui_router
    from app.webhooks.router import router as webhooks_router

    app = FastAPI(title="Pulsyr", lifespan=lifespan)
    from fastapi.staticfiles import StaticFiles
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="pulsyr_session",
        https_only=not settings.debug,
        # SEC-04: endurecer la cookie de sesión.
        same_site="strict",        # corta envío cross-site (mitiga CSRF sobre la sesión).
        max_age=604800,            # 7 días — la sesión expira aunque la cookie persista.
    )
    app.include_router(auth_router)
    app.include_router(setup_router)
    app.include_router(accounts_router)
    app.include_router(projects_router)
    app.include_router(items_router, prefix="/api/v1")
    app.include_router(scopes_router, prefix="/api/v1")
    app.include_router(threads_router, prefix="/api/v1")
    # Webhooks en la raíz (/webhooks/sentry, /webhooks/github) — URLs externas limpias,
    # como /mcp. No van bajo /api/v1 (no son API versionada de cliente).
    app.include_router(webhooks_router)
    app.include_router(management_router)
    app.include_router(ui_router)

    from app.mcp.server import mount_mcp
    mount_mcp(app)

    # Manejo de errores: catch-all de Exception como red de último recurso (REST/UI).
    # Un handler para `Exception` SOLO se invoca cuando ningún handler más específico
    # matchea: FastAPI ya trae handlers built-in para HTTPException (devuelve su status +
    # detail) y RequestValidationError (422 con los errores de validación), que tienen
    # prioridad sobre este. Así, este solo atrapa los errores 500 inesperados.
    app.add_exception_handler(Exception, _unhandled_exception_handler)
    return app


app = create_app()
