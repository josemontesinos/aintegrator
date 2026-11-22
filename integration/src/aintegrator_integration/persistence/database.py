"""SQLAlchemy 2 async engine/session setup."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

__all__ = ["Base", "SessionMaker", "init_db", "make_engine", "make_session_maker"]


class Base(DeclarativeBase):
    """Declarative base for all integration tables."""


def make_engine(url: str) -> AsyncEngine:
    engine = create_async_engine(url, pool_pre_ping=True)
    if url.startswith("sqlite"):
        # SQLite ignores foreign keys by default; enforce them so tests catch
        # the same integrity errors that production Postgres raises.
        @event.listens_for(engine.sync_engine, "connect")
        def enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    """Create tables directly (tests only; production uses Alembic)."""
    from aintegrator_integration.persistence import models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


SessionMaker = async_sessionmaker[AsyncSession]


@asynccontextmanager
async def session_scope(maker: SessionMaker) -> AsyncIterator[AsyncSession]:
    session = maker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
