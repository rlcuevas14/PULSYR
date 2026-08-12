"""Privacy-conscious application monitoring helpers."""

from typing import Any

import sentry_sdk
from sentry_sdk.types import Event

from app.config import settings

_initialized = False
_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "x-csrf-token"}


def scrub_event(event: Event, _hint: dict[str, Any]) -> Event:
    """Remove request bodies, identity and credentials before an event leaves Pulsyr."""
    event.pop("user", None)
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("cookies", None)
        request.pop("query_string", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                key: value
                for key, value in headers.items()
                if key.lower() not in _SENSITIVE_HEADERS
            }
    return event


def init_observability() -> bool:
    """Initialize Sentry once when a DSN is explicitly configured."""
    global _initialized
    if _initialized or not settings.sentry_dsn:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.deployment_environment,
        release=settings.release or None,
        send_default_pii=False,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        before_send=scrub_event,
    )
    _initialized = True
    return True


def capture_exception(exc: Exception, **tags: str) -> None:
    """Capture an operational exception with non-sensitive correlation tags."""
    if not settings.sentry_dsn:
        return
    with sentry_sdk.new_scope() as scope:
        for key, value in tags.items():
            scope.set_tag(key, value)
        sentry_sdk.capture_exception(exc)
