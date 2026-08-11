"""Webhooks Sentry + GitHub (firmados). Sin auth de sesión: la firma HMAC es la auth."""

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.webhooks import connection, service

logger = logging.getLogger("pulsyr.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/sentry/{token}")
async def sentry_webhook_tokened(
    token: str, request: Request, db: AsyncSession = Depends(get_db)
) -> Response:
    """Webhook de entrada por cuenta (spec 2026-07-10). El token enruta a la cuenta;
    HMAC solo si la cuenta guardó client_secret (modo firmado). Siempre fast-ack:
    cero llamadas salientes aquí (Sentry desactiva webhooks que hacen timeout)."""
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


@router.post("/github")
async def github_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
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
