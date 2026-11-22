"""Shared fixtures: in-memory SQLite database for service tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from aintegrator_integration.persistence import (
    SessionMaker,
    init_db,
    make_engine,
    make_session_maker,
)


@pytest.fixture
async def session_maker() -> AsyncIterator[SessionMaker]:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield make_session_maker(engine)
    await engine.dispose()
