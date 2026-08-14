import json
import logging

import pytest
from httpx import ASGITransport, AsyncClient

from app.logging_config import JsonFormatter
from app.main import create_app


@pytest.mark.asyncio
async def test_every_response_has_a_request_id_and_preserves_a_safe_inbound_id(caplog):
    caplog.set_level(logging.INFO, logger="pulsyr.http")
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/health/live?private=value",
            headers={"X-Request-ID": "edge-01.trace_2"},
        )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "edge-01.trace_2"
    record = next(record for record in caplog.records if record.name == "pulsyr.http")
    assert record.event == "http_request_completed"
    assert record.request_id == "edge-01.trace_2"
    assert record.http_method == "GET"
    assert record.http_route == "/health/live"
    assert record.http_status == 200
    assert record.duration_ms >= 0
    assert "private=value" not in record.getMessage()


@pytest.mark.asyncio
async def test_invalid_inbound_request_id_is_replaced():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/live", headers={"X-Request-ID": "bad id"})

    request_id = response.headers["x-request-id"]
    assert request_id != "bad id"
    assert len(request_id) == 32


def test_json_formatter_emits_bounded_structured_fields():
    record = logging.LogRecord(
        name="pulsyr.http",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="http_request_completed",
        args=(),
        exc_info=None,
    )
    record.event = "http_request_completed"
    record.request_id = "req-1"
    record.http_method = "GET"
    record.http_route = "/api/v1/items/{item_id}"
    record.http_status = 200
    record.duration_ms = 4.2

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "http_request_completed"
    assert payload["request_id"] == "req-1"
    assert payload["http_route"] == "/api/v1/items/{item_id}"
    assert payload["duration_ms"] == 4.2
    assert "pathname" not in payload
    assert "query" not in payload


def test_json_formatter_does_not_interpolate_potentially_private_arguments():
    record = logging.LogRecord(
        name="pulsyr.mcp",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="tool %s args=%s failed",
        args=("create_item", {"title": "private roadmap"}),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "tool %s args=%s failed"
    assert "private roadmap" not in json.dumps(payload)
