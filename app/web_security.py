"""Browser security, CSRF, caching, and delivery policies for the private app."""

from __future__ import annotations

import hmac
import re
import secrets
from collections.abc import Awaitable, Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse, Response

from app.config import settings

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
SESSION_COOKIE = "pulsyr_session"
CSRF_COOKIE = "pulsyr_csrf" if settings.debug else "__Host-pulsyr_csrf"
HASHED_ASSET = re.compile(r"^/static/assets/[^/]+\.[a-f0-9]{12}\.(?:css|js)$")


class CsrfMiddleware(BaseHTTPMiddleware):
    """Double-submit CSRF protection for every cookie-authenticated mutation."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        csrf_token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
        request.state.csrf_token = csrf_token

        has_session = bool(request.cookies.get(SESSION_COOKIE))
        exempt_path = request.url.path.startswith(("/mcp", "/webhooks/"))
        bearer_request = request.headers.get("authorization", "").lower().startswith("bearer ")
        if request.method not in SAFE_METHODS and has_session and not exempt_path and not bearer_request:
            provided = request.headers.get("x-csrf-token", "")
            if not provided:
                # Reading the body first lets Starlette replay it to the endpoint after
                # form parsing, including multipart upload forms.
                await request.body()
                content_type = request.headers.get("content-type", "")
                if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
                    form = await request.form()
                    value = form.get("csrf_token")
                    provided = value if isinstance(value, str) else ""
            if not provided or not hmac.compare_digest(provided, csrf_token):
                return PlainTextResponse(
                    "CSRF validation failed",
                    status_code=403,
                    headers={"X-Robots-Tag": "noindex, nofollow"},
                )

        response = await call_next(request)
        if CSRF_COOKIE not in request.cookies:
            response.set_cookie(
                CSRF_COOKIE,
                csrf_token,
                secure=not settings.debug,
                httponly=True,
                samesite="strict",
                path="/",
            )
        return response


class ResponsePolicyMiddleware(BaseHTTPMiddleware):
    """Apply security and cache policy even to short-circuited middleware responses."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        headers = response.headers
        # Dynamic brand/project colors still need inline styles. Executable code is
        # external and self-hosted, so scripts need no inline exception.
        headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
            "form-action 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "font-src 'self'; connect-src 'self'; frame-src 'self'; manifest-src 'self'"
        )
        headers["X-Frame-Options"] = "DENY"
        headers["X-Content-Type-Options"] = "nosniff"
        headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"
        )
        if not settings.debug:
            headers["Strict-Transport-Security"] = "max-age=31536000"

        if HASHED_ASSET.match(request.url.path):
            headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif request.url.path.startswith("/static/"):
            headers["Cache-Control"] = "public, max-age=3600"
        else:
            headers["Cache-Control"] = "private, no-store"
        return response
