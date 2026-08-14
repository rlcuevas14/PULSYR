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
    ],
)
def test_database_resource_settings_are_bounded(overrides):
    with pytest.raises(ValidationError):
        Settings(debug=True, secret_key="test-secret", **overrides)
