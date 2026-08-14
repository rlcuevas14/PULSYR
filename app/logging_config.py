import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.config import settings

request_id_context: ContextVar[str] = ContextVar("request_id", default="")

_STANDARD_ATTRIBUTES = set(logging.makeLogRecord({}).__dict__)
_STRUCTURED_ATTRIBUTES = {
    "component",
    "duration_ms",
    "http_method",
    "http_route",
    "http_status",
    "job_id",
    "job_kind",
    "request_id",
}


class JsonFormatter(logging.Formatter):
    """Emit machine-readable logs without request bodies, URLs, or identities."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            # Keep templates, not interpolated arguments: callers occasionally log
            # resource names or tool arguments that can contain customer content.
            "event": getattr(record, "event", str(record.msg)),
            "environment": settings.deployment_environment,
        }
        if settings.release:
            payload["release"] = settings.release

        context_request_id = request_id_context.get()
        if context_request_id:
            payload["request_id"] = context_request_id

        for attribute in _STRUCTURED_ATTRIBUTES:
            if attribute in _STANDARD_ATTRIBUTES:
                continue
            value = getattr(record, attribute, None)
            if value not in (None, ""):
                payload[attribute] = value

        if record.exc_info:
            exception_type = record.exc_info[0]
            payload["exception_type"] = exception_type.__name__ if exception_type else "Exception"
            if settings.debug:
                payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    """Configure root output consistently under Uvicorn, scripts, and tests."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if settings.debug else logging.INFO)
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    formatter = JsonFormatter()
    for handler in root.handlers:
        handler.setFormatter(formatter)
