from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from scout_core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def make_engine(url: str | None = None, *, pooled: bool = True) -> AsyncEngine:
    url = url or get_settings().database_url
    if pooled:
        return create_async_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
    # Workers call asyncio.run() per task, so connections must not outlive the loop.
    return create_async_engine(url, poolclass=NullPool)


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _engine, _sessionmaker
    if _sessionmaker is None:
        _engine = make_engine()
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _sessionmaker


@asynccontextmanager
async def worker_session() -> AsyncIterator[AsyncSession]:
    """A session on a throwaway engine, safe to use inside asyncio.run() in a Celery task."""
    engine = make_engine(pooled=False)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()
