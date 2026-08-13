import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.models import ApiToken, User  # noqa: F401
from app.accounts.models import Account, AccountSubscription  # noqa: F401
from app.config import settings
from app.database import Base
from app.items.models import (  # noqa: F401
    AiEnrichment,
    Item,
    ItemComment,
    ItemEvent,
    ItemRelationship,
)
from app.jobs.models import AgentRun  # noqa: F401
from app.scopes.models import Scope  # noqa: F401
from app.threads.models import Thread, ThreadArtifact  # noqa: F401
from app.webhooks.models import SentryIssue  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = settings.database_url.replace("+asyncpg", "")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
