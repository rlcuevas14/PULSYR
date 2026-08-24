"""Outbound Paddle Billing API.

Hand-rolled on httpx for the same reason MCP is: five endpoints do not justify a
package, and a thin surface is easier to mock than an SDK. Every call raises
PaddleNotConfigured when no key is set, so a caller has exactly one branch to
handle rather than a scatter of None checks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from app.config import settings

if TYPE_CHECKING:
    from typing import Any as PlanPrice

_SANDBOX = "https://sandbox-api.paddle.com"
_PRODUCTION = "https://api.paddle.com"
_TIMEOUT = 15.0


class PaddleError(RuntimeError):
    """Paddle answered, and the answer was not usable."""


class PaddleNotConfigured(PaddleError):
    """No API key. The caller should hide its action rather than fail loudly."""


def configured() -> bool:
    return bool(settings.paddle_api_key)


def _base_url() -> str:
    return _PRODUCTION if settings.paddle_environment == "production" else _SANDBOX


async def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    if not configured():
        raise PaddleNotConfigured("PADDLE_API_KEY is not set")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.request(
            method,
            f"{_base_url()}{path}",
            headers={"Authorization": f"Bearer {settings.paddle_api_key}"},
            json=payload,
        )
    if response.status_code >= 400:
        raise PaddleError(f"Paddle {method} {path} returned {response.status_code}")
    return response.json().get("data")


async def list_plan_prices() -> list[PlanPrice]:  # noqa: F821
    return await _request("GET", "/prices") or []
