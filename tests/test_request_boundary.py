import json
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.auth import rate_limit
from app.auth.rate_limit import RateLimitDecision, client_address, privacy_key
from app.config import settings
from app.main import create_app
from app.route_inventory import route_inventory


@pytest.fixture(autouse=True)
async def clear_shared_rate_limits(test_engine):
    async with test_engine.begin() as connection:
        await connection.execute(text("TRUNCATE rate_limit_buckets"))


async def test_api_and_machine_endpoints_reject_wrong_media_type(monkeypatch):
    monkeypatch.setattr(rate_limit, "limit_client", AsyncMock(return_value=RateLimitDecision(True, 1)))
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        api = await client.post("/api/v1/items", content=b"{}", headers={"Content-Type": "text/plain"})
        mcp = await client.post("/mcp", content=b"{}", headers={"Content-Type": "text/plain"})
        webhook = await client.post(
            "/webhooks/github", content=b"{}", headers={"Content-Type": "text/plain"}
        )

    for response in (api, mcp, webhook):
        assert response.status_code == 415
        assert response.json()["error"]["code"] == "unsupported_media_type"
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


async def test_declared_and_streamed_oversized_bodies_are_rejected(monkeypatch):
    monkeypatch.setattr(settings, "mcp_max_body_bytes", 8)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        declared = await client.post(
            "/mcp",
            content=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": "99"},
        )

        async def chunks():
            yield b"12345"
            yield b"67890"

        streamed = await client.post(
            "/mcp", content=chunks(), headers={"Content-Type": "application/json"}
        )

    assert declared.status_code == 413
    assert streamed.status_code == 413
    assert declared.json()["error"]["code"] == "body_too_large"


async def test_valid_json_body_is_replayed_to_endpoint(monkeypatch):
    monkeypatch.setattr(
        "app.mcp.server.limit_client", AsyncMock(return_value=RateLimitDecision(True, 1))
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        )

    assert response.status_code == 401
    assert response.json() == {"error": "Bearer token required"}


def test_forwarded_address_requires_a_trusted_direct_peer(monkeypatch):
    app = create_app()

    def request_for(peer: str):
        from starlette.requests import Request

        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/login/github",
                "headers": [(b"cf-connecting-ip", b"203.0.113.9")],
                "client": (peer, 1234),
                "scheme": "https",
                "server": ("test", 443),
                "query_string": b"",
                "root_path": "",
                "app": app,
            }
        )

    monkeypatch.setattr(settings, "trusted_proxy_cidrs", "")
    assert client_address(request_for("10.0.0.5")) == "10.0.0.5"
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", "10.0.0.0/24")
    assert client_address(request_for("10.0.0.5")) == "203.0.113.9"


def test_privacy_key_never_contains_the_raw_identity():
    key = privacy_key("login", "Person@Example.com")
    assert len(key) == 64
    assert "person" not in key


async def test_password_failures_are_limited_by_identity_and_ip(client, monkeypatch):
    monkeypatch.setattr(settings, "password_rate_limit_attempts", 1)
    monkeypatch.setattr(settings, "password_ip_rate_limit_attempts", 10)

    first = await client.post("/login", data={"email": "victim@example.com", "password": "wrong"})
    second = await client.post("/login", data={"email": "victim@example.com", "password": "wrong"})

    assert first.status_code == 401
    assert second.status_code == 429
    assert 1 <= int(second.headers["retry-after"]) <= settings.oauth_rate_limit_window_seconds


async def test_mcp_shared_limit_returns_retry_after(client, monkeypatch):
    monkeypatch.setattr(settings, "mcp_rate_limit_attempts", 1)

    first = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    second = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    assert first.status_code == 401
    assert second.status_code == 429
    assert "retry-after" in second.headers


def test_route_inventory_covers_every_operation_and_critical_trust_class():
    rows = route_inventory(create_app())
    by_operation = {(row["method"], row["path"]): row for row in rows}

    assert len(rows) >= 100
    assert by_operation[("GET", "/health/live")]["trust_class"] == "public"
    assert by_operation[("GET", "/admin/accounts")]["trust_class"] == "superadmin_session"
    assert by_operation[("POST", "/api/v1/items")]["trust_class"] == "session_or_write_token"
    assert by_operation[("POST", "/webhooks/github")]["trust_class"] == "webhook_signature"
    assert by_operation[("POST", "/mcp")]["trust_class"] == "manual_api_token"
    assert json.dumps(rows, sort_keys=True)
