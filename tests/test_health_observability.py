from unittest.mock import AsyncMock, Mock

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app import database, main, observability
from app.config import Settings
from app.main import create_app
from app.observability import scrub_event


@pytest.mark.asyncio
async def test_liveness_is_public_but_noindex():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "pulsyr"}
    assert response.headers["x-robots-tag"] == "noindex, nofollow"


@pytest.mark.asyncio
async def test_readiness_reports_dependency_state_without_details(monkeypatch):
    ready = AsyncMock(return_value=True)
    monkeypatch.setattr(main, "_database_ready", ready)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}

    ready.return_value = False
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "error"}
    assert "exception" not in response.text.lower()


def test_sentry_event_scrubber_removes_private_content():
    event = {
        "user": {"email": "owner@example.com"},
        "request": {
            "data": {"backlog": "private"},
            "cookies": {"session": "secret"},
            "query_string": "token=secret",
            "headers": {
                "Authorization": "Bearer secret",
                "Cookie": "session=secret",
                "User-Agent": "test",
            },
        },
    }
    scrubbed = scrub_event(event, {})
    assert "user" not in scrubbed
    assert scrubbed["request"] == {"headers": {"User-Agent": "test"}}


def test_sentry_initialization_sets_environment_release_and_no_pii(monkeypatch):
    init = Mock()
    monkeypatch.setattr(observability.sentry_sdk, "init", init)
    monkeypatch.setattr(observability, "_initialized", False)
    monkeypatch.setattr(observability.settings, "sentry_dsn", "https://public@example.invalid/1")
    monkeypatch.setattr(observability.settings, "deployment_environment", "staging")
    monkeypatch.setattr(observability.settings, "release", "sha-123")
    monkeypatch.setattr(observability.settings, "sentry_traces_sample_rate", 0.05)

    assert observability.init_observability() is True
    kwargs = init.call_args.kwargs
    assert kwargs["environment"] == "staging"
    assert kwargs["release"] == "sha-123"
    assert kwargs["send_default_pii"] is False
    assert kwargs["traces_sample_rate"] == 0.05
    assert kwargs["before_send"] is observability.scrub_event


@pytest.mark.asyncio
async def test_database_readiness_success_and_failure(monkeypatch):
    class SessionContext:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, *_args):
            return None

    session = AsyncMock()
    monkeypatch.setattr(database, "SessionFactory", lambda: SessionContext(session))
    assert await main._database_ready() is True

    session.execute.side_effect = RuntimeError("database unavailable")
    assert await main._database_ready() is False


def test_capture_exception_adds_only_explicit_tags(monkeypatch):
    scope = Mock()
    context = Mock()
    context.__enter__ = Mock(return_value=scope)
    context.__exit__ = Mock(return_value=False)
    capture = Mock()
    monkeypatch.setattr(observability.settings, "sentry_dsn", "https://public@example.invalid/1")
    monkeypatch.setattr(observability.sentry_sdk, "new_scope", Mock(return_value=context))
    monkeypatch.setattr(observability.sentry_sdk, "capture_exception", capture)
    error = RuntimeError("controlled")

    observability.capture_exception(error, component="test", request_id="abc")

    scope.set_tag.assert_any_call("component", "test")
    scope.set_tag.assert_any_call("request_id", "abc")
    capture.assert_called_once_with(error)


@pytest.mark.parametrize(
    "overrides",
    [{"sentry_traces_sample_rate": 1.1}, {"readiness_timeout_seconds": 0}],
)
def test_observability_numeric_settings_are_bounded(overrides):
    with pytest.raises(ValidationError):
        Settings(debug=True, secret_key="test-secret", **overrides)
