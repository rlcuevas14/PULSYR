"""Bound untrusted HTTP bodies and enforce media types on machine endpoints."""

from __future__ import annotations

from dataclasses import dataclass

from starlette.datastructures import Headers
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings
from app.request_context import request_id_from_scope

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class BodyPolicy:
    max_bytes: int
    media_types: frozenset[str] | None = None


def body_policy(path: str) -> BodyPolicy:
    if path.startswith("/webhooks/"):
        return BodyPolicy(settings.webhook_max_body_bytes, frozenset({"application/json"}))
    if path == "/mcp":
        return BodyPolicy(settings.mcp_max_body_bytes, frozenset({"application/json"}))
    if path == "/ui/management/documentos/upload":
        return BodyPolicy(settings.upload_max_body_bytes, frozenset({"multipart/form-data"}))
    if path.startswith("/api/"):
        return BodyPolicy(settings.request_max_body_bytes, frozenset({"application/json"}))
    return BodyPolicy(settings.request_max_body_bytes)


def _boundary_error(scope: Scope, status_code: int, code: str, message: str) -> Response:
    path = scope.get("path", "")
    headers = {"X-Request-ID": request_id_from_scope(scope), "X-Robots-Tag": "noindex, nofollow"}
    if path.startswith(("/api/", "/mcp", "/webhooks/")):
        return JSONResponse(
            {"error": {"code": code, "message": message, "request_id": headers["X-Request-ID"]}},
            status_code=status_code,
            headers=headers,
        )
    return PlainTextResponse(message, status_code=status_code, headers=headers)


class RequestBoundaryMiddleware:
    """Read mutation bodies once with a hard ceiling, then replay them downstream."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _BODY_METHODS:
            await self.app(scope, receive, send)
            return

        policy = body_policy(scope.get("path", ""))
        headers = Headers(scope=scope)
        raw_length = headers.get("content-length")
        if raw_length:
            try:
                declared_length = int(raw_length)
            except ValueError:
                response = _boundary_error(scope, 400, "invalid_content_length", "Invalid Content-Length")
                await response(scope, receive, send)
                return
            if declared_length < 0:
                response = _boundary_error(scope, 400, "invalid_content_length", "Invalid Content-Length")
                await response(scope, receive, send)
                return
            if declared_length > policy.max_bytes:
                response = _boundary_error(scope, 413, "body_too_large", "Request body too large")
                await response(scope, receive, send)
                return

        chunks: list[bytes] = []
        body_size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            body_size += len(chunk)
            if body_size > policy.max_bytes:
                response = _boundary_error(scope, 413, "body_too_large", "Request body too large")
                await response(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        body = b"".join(chunks)
        if body and policy.media_types:
            media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            # Keep compatibility with existing signed senders that omit the header;
            # reject a declared, contradictory media type before parsing.
            if media_type and media_type not in policy.media_types:
                response = _boundary_error(
                    scope,
                    415,
                    "unsupported_media_type",
                    f"Content-Type must be one of: {', '.join(sorted(policy.media_types))}",
                )
                await response(scope, receive, send)
                return

        replay = _single_message_receive({"type": "http.request", "body": body, "more_body": False})
        await self.app(scope, replay, send)


def _single_message_receive(message: Message) -> Receive:
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if not delivered:
            delivered = True
            return message
        return {"type": "http.disconnect"}

    return receive
