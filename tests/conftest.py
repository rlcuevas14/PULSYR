import os
from typing import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app import database
from app.billing import paddle
from app.database import Base, get_db
from app.main import create_app
from app.web_security import CSRF_COOKIE

_TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "")


class CsrfAsyncClient(AsyncClient):
    """Exercise browser mutations with the same double-submit header as the UI."""

    async def request(self, method, url, **kwargs):
        if method.upper() not in {"GET", "HEAD", "OPTIONS", "TRACE"}:
            token = next(
                (cookie.value for cookie in self.cookies.jar if cookie.name == CSRF_COOKIE),
                None,
            )
            if token is None:
                token = "test-csrf-token"
                self.cookies.set(CSRF_COOKIE, token)
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("X-CSRF-Token", token)
            kwargs["headers"] = headers
        return await super().request(method, url, **kwargs)


@pytest.fixture(autouse=True)
def _refuse_unmocked_paddle_calls(monkeypatch):
    """A test that forgets to mock a Paddle call must fail here, not reach the
    payment provider. Two tests in this suite escaped to the real sandbox API
    before this existed, and both passed anyway because the 403 they got back
    happened to produce the error they tolerated."""
    def _refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(
            f"unmocked Paddle call to {request.method} {request.url}: "
            "monkeypatch the paddle function this route calls"
        )

    monkeypatch.setattr(paddle, "_transport", httpx.MockTransport(_refuse))


@pytest.fixture(scope="session")
def pg_url() -> str:
    if _TEST_DB_URL:
        yield _TEST_DB_URL
    else:
        with PostgresContainer("pgvector/pgvector:pg16") as pg:
            raw = pg.get_connection_url()
            yield raw.replace("psycopg2", "asyncpg").replace(
                "postgresql://", "postgresql+asyncpg://"
            ).replace("postgresql+asyncpg+asyncpg://", "postgresql+asyncpg://")


@pytest_asyncio.fixture(scope="session")
async def test_engine(pg_url):
    engine = create_async_engine(pg_url, echo=False)
    # Try to create the vector extension — skip if pgvector not installed locally.
    async with engine.connect() as conn:
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.commit()
        except Exception:
            await conn.rollback()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # search_vector is a GENERATED column that lives only in migration v0002
    # (not in the ORM); create_all does not create it. We add it here so full-text
    # works in every test (search, MCP, relationship resolution).
    async with engine.begin() as conn:
        await conn.execute(text("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='items' AND column_name='search_vector'
                ) THEN
                    ALTER TABLE items ADD COLUMN search_vector tsvector
                    GENERATED ALWAYS AS (
                        setweight(to_tsvector('spanish', coalesce(title, '')), 'A') ||
                        setweight(to_tsvector('spanish', coalesce(summary_md, '')), 'B')
                    ) STORED;
                    CREATE INDEX items_search_gin ON items USING GIN (search_vector);
                END IF;
            END $$;
        """))
    # Truncate all data tables so tests are repeatable across runs. Truncating
    # `accounts` cascades to users, projects, project_members, api_tokens and every
    # project-scoped table (items, scopes, threads, sentry_issues, agent_runs).
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE accounts RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db(test_engine) -> AsyncGenerator[AsyncSession, None]:
    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)
    async with TestSession() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(test_engine) -> AsyncGenerator[AsyncClient, None]:
    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)
    original_session_factory = database.SessionFactory
    database.SessionFactory = TestSession

    async def override_get_db():
        async with TestSession() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db

    try:
        async with TestSession() as limiter_db:
            await limiter_db.execute(text("TRUNCATE rate_limit_buckets"))
            await limiter_db.commit()
        transport = ASGITransport(app=app)
        async with CsrfAsyncClient(transport=transport, base_url="http://test") as ac:
            ac.app = app  # expose app for test introspection
            yield ac
    finally:
        database.SessionFactory = original_session_factory
