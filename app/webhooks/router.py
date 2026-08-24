"""Webhooks Sentry + GitHub (firmados). Sin auth de sesión: la firma HMAC es la auth."""

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts import plans
from app.auth.rate_limit import limit_client
from app.config import settings
from app.database import get_db
from app.projects.modules import is_module_enabled
from app.webhooks import connection, service

logger = logging.getLogger("pulsyr.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# The three that carry a full subscription entity. Everything else Paddle sends is
# acknowledged and dropped: billing state lives in these.
PADDLE_SUBSCRIPTION_EVENTS = frozenset(
    {"subscription.created", "subscription.updated", "subscription.canceled"}
)


async def _limited(request: Request) -> JSONResponse | None:
    decision = await limit_client(
        request,
        action="webhook",
        limit=settings.webhook_rate_limit_attempts,
        window_seconds=settings.machine_rate_limit_window_seconds,
    )
    if decision.allowed:
        return None
    return JSONResponse(
        {"error": "rate limit exceeded"},
        status_code=429,
        headers={"Retry-After": str(decision.retry_after)},
    )


@router.post("/sentry/{token}")
async def sentry_webhook_tokened(
    token: str, request: Request, db: AsyncSession = Depends(get_db)
) -> Response:
    """Webhook de entrada por cuenta (spec 2026-07-10). El token enruta a la cuenta;
    HMAC solo si la cuenta guardó client_secret (modo firmado). Siempre fast-ack:
    cero llamadas salientes aquí (Sentry desactiva webhooks que hacen timeout)."""
    if limited := await _limited(request):
        return limited
    conn = await connection.get_by_token(db, token)
    if conn is None:
        return JSONResponse({"error": "unknown webhook token"}, status_code=404)
    body = await request.body()
    if conn.client_secret:
        sig = request.headers.get("sentry-hook-signature")
        if not service.verify_sentry_signature(conn.client_secret, body, sig):
            return JSONResponse({"error": "invalid signature"}, status_code=401)
    try:
        payload = json.loads(body)
        parsed = service.parse_sentry_payload(payload)
        project = await connection.route_project(db, conn.account_id, parsed["slug"])
        if project is not None and not await is_module_enabled(db, project.id, "incidents"):
            logger.info(
                "sentry webhook skipped: module_disabled project_id=%s", project.id
            )
            return JSONResponse({
                "accepted": True,
                "status": "ignored",
                "reason": "module_disabled",
            })
        result = await service.ingest_sentry(
            db, payload, account_id=conn.account_id,
            project_id=project.id if project else None,
        )
        await db.commit()
    except (ValueError, json.JSONDecodeError) as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    if project is None:
        logger.warning("sentry webhook: unmatched slug %r for account %s",
                       parsed["slug"], conn.account_id)
    return JSONResponse(result)


@router.post("/sentry")
async def sentry_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    if limited := await _limited(request):
        return limited
    if not settings.sentry_client_secret:
        return JSONResponse({"error": "Sentry webhook not configured"}, status_code=503)
    body = await request.body()
    sig = request.headers.get("sentry-hook-signature")
    if not service.verify_sentry_signature(settings.sentry_client_secret, body, sig):
        return JSONResponse({"error": "invalid signature"}, status_code=401)
    try:
        payload = json.loads(body)
        result = await service.ingest_sentry(db, payload)
        await db.commit()
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return JSONResponse(result)


@router.post("/paddle")
async def paddle_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    """Billing events from Paddle. The signature is the auth; there is no token in the
    path because one Paddle account bills every tenant and the payload names the account.

    Only a 2xx tells Paddle the event was delivered, so every failure here answers
    non-2xx on purpose: the delivery stays in the notification log and gets retried
    instead of a paid subscription vanishing silently."""
    if limited := await _limited(request):
        return limited
    if not settings.paddle_webhook_secret:
        return JSONResponse({"error": "Paddle webhook not configured"}, status_code=503)
    body = await request.body()
    sig = request.headers.get("paddle-signature")
    if not service.verify_paddle_signature(settings.paddle_webhook_secret, body, sig):
        return JSONResponse({"error": "invalid signature"}, status_code=401)
    try:
        event = json.loads(body)
        event_type = event.get("event_type", "")
        if event_type not in PADDLE_SUBSCRIPTION_EVENTS:
            return JSONResponse({"accepted": True, "status": "ignored", "event": event_type})
        parsed = service.parse_paddle_subscription(event)
        result = await plans.apply_paddle_subscription(
            db,
            account_id=parsed.account_id,
            plan_code=parsed.plan_code,
            paddle_status=parsed.paddle_status,
            subscription_id=parsed.subscription_id,
            customer_id=parsed.customer_id,
            occurred_at=parsed.occurred_at,
        )
        await db.commit()
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning("paddle webhook rejected: %s", e)
        return JSONResponse({"error": str(e)}, status_code=422)
    logger.info("paddle webhook %s account=%s %s", event_type, parsed.account_id, result)
    return JSONResponse({"accepted": True, "status": result})


@router.post("/github")
async def github_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    if limited := await _limited(request):
        return limited
    if not settings.github_webhook_secret:
        return JSONResponse({"error": "GitHub webhook not configured"}, status_code=503)
    body = await request.body()
    sig = request.headers.get("x-hub-signature-256")
    if not service.verify_github_signature(settings.github_webhook_secret, body, sig):
        return JSONResponse({"error": "invalid signature"}, status_code=401)

    event = request.headers.get("x-github-event", "")
    if event not in ("push",):
        return JSONResponse({"ignored": event})
    payload = json.loads(body)
    result = await service.process_github_push(db, payload)
    await db.commit()
    return JSONResponse(result)
