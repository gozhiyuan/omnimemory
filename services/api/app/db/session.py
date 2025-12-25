"""Async database session utilities."""

from __future__ import annotations

from contextlib import asynccontextmanager
from functools import lru_cache
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings


def build_async_engine() -> AsyncEngine:
    """Create a new async SQLAlchemy engine."""

    settings = get_settings()
    url = (
        f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )
    return create_async_engine(url, future=True, echo=False)


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Return a cached async SQLAlchemy engine."""

    return build_async_engine()


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return a cached sessionmaker bound to the engine."""

    engine = get_engine()
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Provide an async session for FastAPI dependencies."""

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session


async def database_healthcheck() -> bool:
    """Run a lightweight query to validate connectivity."""

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(text("SELECT 1"))
        value = result.scalar()
        return bool(value == 1)


@asynccontextmanager
async def isolated_session() -> AsyncIterator[AsyncSession]:
    """Yield a session backed by a one-off engine for background tasks."""

    engine = build_async_engine()
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            yield session
    finally:
        await engine.dispose()
