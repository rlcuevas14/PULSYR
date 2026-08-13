"""Small bounded rate limiter for OAuth entry points.

The hosted deployment runs one application process, so an in-process fixed window
is sufficient for the first public tier. Keys are HMAC digests: raw visitor IPs are
never retained in memory or logs. Edge rate limiting can later complement this
without changing the auth contract.
"""

import asyncio
import hashlib
import hmac
import time
from dataclasses import dataclass

from fastapi import Request

from app.config import settings


@dataclass
class _Window:
    started: float
    attempts: int


class OAuthRateLimiter:
    def __init__(self) -> None:
        self._windows: dict[str, _Window] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _client_key(request: Request, action: str) -> str:
        # Cloudflare overwrites CF-Connecting-IP at the edge. Without that header,
        # use the direct peer supplied by the ASGI server.
        address = request.headers.get("cf-connecting-ip")
        if not address:
            address = request.client.host if request.client else "unknown"
        digest = hmac.new(
            settings.secret_key.encode(),
            f"{action}:{address}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return digest

    async def allow(self, request: Request, action: str) -> bool:
        now = time.monotonic()
        window_seconds = settings.oauth_rate_limit_window_seconds
        key = self._client_key(request, action)
        async with self._lock:
            current = self._windows.get(key)
            if current is None or now - current.started >= window_seconds:
                self._windows[key] = _Window(started=now, attempts=1)
                self._prune(now, window_seconds)
                return True
            if current.attempts >= settings.oauth_rate_limit_attempts:
                return False
            current.attempts += 1
            return True

    def _prune(self, now: float, window_seconds: int) -> None:
        if len(self._windows) < 2048:
            return
        expired = [key for key, value in self._windows.items() if now - value.started >= window_seconds]
        for key in expired:
            self._windows.pop(key, None)

    def reset(self) -> None:
        self._windows.clear()


oauth_rate_limiter = OAuthRateLimiter()
