from unittest.mock import AsyncMock

import pytest

from app import database


def test_engine_options_bound_postgresql_work(monkeypatch):
    monkeypatch.setattr(database.settings, "database_url", "postgresql+asyncpg://db/example")
    monkeypatch.setattr(database.settings, "db_pool_size", 7)
    monkeypatch.setattr(database.settings, "db_max_overflow", 3)
    monkeypatch.setattr(database.settings, "db_pool_timeout_seconds", 4.0)
    monkeypatch.setattr(database.settings, "db_pool_recycle_seconds", 900)
    monkeypatch.setattr(database.settings, "db_statement_timeout_seconds", 12.0)

    assert database.engine_options() == {
        "echo": database.settings.debug,
        "pool_pre_ping": True,
        "pool_size": 7,
        "max_overflow": 3,
        "pool_timeout": 4.0,
        "pool_recycle": 900,
        "connect_args": {"command_timeout": 12.0},
    }


@pytest.mark.asyncio
async def test_request_session_rolls_back_before_propagating_failure(monkeypatch):
    session = AsyncMock()

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(database, "SessionFactory", SessionContext)
    dependency = database.get_db()
    assert await anext(dependency) is session

    with pytest.raises(RuntimeError, match="controlled"):
        await dependency.athrow(RuntimeError("controlled"))

    session.rollback.assert_awaited_once_with()

