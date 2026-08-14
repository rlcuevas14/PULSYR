from datetime import datetime

from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings


def engine_options() -> dict:
    """Return bounded production pool options without leaking connection details."""
    options: dict = {"echo": settings.debug, "pool_pre_ping": True}
    if settings.database_url.startswith("postgresql"):
        options.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            pool_recycle=settings.db_pool_recycle_seconds,
            connect_args={"command_timeout": settings.db_statement_timeout_seconds},
        )
    return options


engine = create_async_engine(settings.database_url, **engine_options())
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


async def get_db():
    async with SessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
