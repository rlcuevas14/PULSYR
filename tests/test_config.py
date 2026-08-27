"""Fail-fast guard on insecure SECRET_KEY values.

.env.example ships SECRET_KEY=change-me and promises that startup aborts in
production if the key is left at a placeholder — these tests hold that promise.
"""
import pytest
from pydantic import ValidationError

from app.config import Settings


def test_production_rejects_distributed_placeholder():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(secret_key="change-me", debug=False)


def test_production_rejects_legacy_placeholder():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(secret_key="dev-secret-change-in-production", debug=False)


def test_production_rejects_short_secret():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(secret_key="a" * 31, debug=False)


def test_production_accepts_strong_secret():
    s = Settings(secret_key="x" * 64, debug=False)
    assert s.secret_key == "x" * 64


def test_debug_allows_placeholder_secret():
    s = Settings(secret_key="change-me", debug=True)
    assert s.debug is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"db_pool_size": 0},
        {"db_max_overflow": -1},
        {"db_pool_timeout_seconds": 0},
        {"db_pool_recycle_seconds": 59},
        {"db_statement_timeout_seconds": 301},
        {"trusted_proxy_cidrs": "not-a-network"},
        {"request_max_body_bytes": 100},
        {"mcp_rate_limit_attempts": 10001},
    ],
)
def test_database_resource_settings_are_bounded(overrides):
    with pytest.raises(ValidationError):
        Settings(debug=True, secret_key="test-secret", **overrides)


def _paddle(**overrides):
    return Settings(debug=True, secret_key="test-secret", **overrides)


@pytest.mark.parametrize(
    "environment, key",
    [
        ("sandbox", "pdl_sdbx_apikey_x"),
        ("production", "pdl_live_apikey_x"),
    ],
)
def test_matching_paddle_environment_and_key_start(environment, key):
    assert _paddle(paddle_environment=environment, paddle_api_key=key)


@pytest.mark.parametrize(
    "overrides",
    [
        # The dangerous direction: a live key that would be sent to the sandbox
        # host, where every call 403s with nothing saying why.
        {"paddle_environment": "sandbox", "paddle_api_key": "pdl_live_apikey_x"},
        {"paddle_environment": "production", "paddle_api_key": "pdl_sdbx_apikey_x"},
        {"paddle_environment": "production", "paddle_client_token": "test_abc"},
        {"paddle_environment": "sandbox", "paddle_client_token": "live_abc"},
    ],
)
def test_paddle_credentials_must_match_their_environment(overrides):
    with pytest.raises(ValidationError, match="PADDLE_"):
        _paddle(**overrides)


@pytest.mark.parametrize("value", ["prod", "live", "Production", "PRODUCTION", ""])
def test_a_paddle_environment_that_is_not_exactly_the_two_words_is_refused(value):
    """Every consumer compares against the exact string "production", so any
    other spelling silently means sandbox. Failing at boot is the only place
    that mistake is cheap."""
    with pytest.raises(ValidationError, match="PADDLE_ENVIRONMENT"):
        _paddle(paddle_environment=value)


def test_an_unrecognised_key_prefix_is_left_alone():
    """Refusing to boot over a prefix Paddle introduced after this code was
    written would be a self-inflicted outage over something we do not
    understand."""
    assert _paddle(paddle_environment="production", paddle_api_key="pdl_future_apikey_x")
