import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_config import request_id_context

logger = logging.getLogger("pulsyr.http")

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def new_request_id() -> str:
    return uuid.uuid4().hex


def request_id_from_scope(scope: Scope) -> str:
    state = scope.get("state") or {}
    request_id = state.get("request_id")
    return request_id if isinstance(request_id, str) and request_id else new_request_id()


def _incoming_request_id(scope: Scope) -> str:
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name.lower() == b"x-request-id":
            candidate = raw_value.decode("ascii", errors="ignore")
            if _REQUEST_ID_PATTERN.fullmatch(candidate):
                return candidate
            break
    return new_request_id()


class RequestContextMiddleware:
    """Correlate every HTTP response and emit one bounded access-log event."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        context_token = request_id_context.set(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            route = getattr(scope.get("route"), "path", "unmatched")
            level = (
                logging.ERROR
                if status_code >= 500
                else logging.WARNING
                if status_code >= 400
                else logging.INFO
            )
            logger.log(
                level,
                "http_request_completed",
                extra={
                    "event": "http_request_completed",
                    "request_id": request_id,
                    "http_method": scope["method"],
                    "http_route": route,
                    "http_status": status_code,
                    "duration_ms": duration_ms,
                },
            )
            request_id_context.reset(context_token)

