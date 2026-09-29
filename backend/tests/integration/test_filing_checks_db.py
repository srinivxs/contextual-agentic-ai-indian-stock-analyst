"""Checking a stock for new filings on demand when it is followed (the "Update data" button,
which checks every stock, is tests/integration/test_data_status_db.py).

A follow only queues a ``discover_filings`` job for that one stock; the worker does the rest, and
fetches only filings not already stored (tests/integration/test_filings_db.py). What is checked
here: at most one check per stock per hour (CHECK_COOLDOWN_HOURS), nothing when the switch is
off, and the daily timer skipping a stock checked by hand.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.filings import CHECK_COOLDOWN_HOURS, enqueue_discovery
from tests.helpers import running_app
from tests.integration.auth_helpers import open_session
from tests.integration.conftest import DbConfig, MakeUser

pytestmark = pytest.mark.usefixtures("clean_document_tables")

Factory = async_sessionmaker[AsyncSession]
ORIGIN = {"Origin": "http://localhost:8000"}
CHECK = "/api/v1/stocks/{symbol}/filings/check"


def check_url(symbol: str = "TCS") -> str:
    return CHECK.format(symbol=symbol)


def switched_on(db_config: DbConfig) -> Settings:
    return db_config.settings(filings_discovery=True)


async def sign_in(make_user: MakeUser, session_factory: Factory) -> tuple[UUID, dict[str, str]]:
    user_id = await make_user()
    token = await open_session(session_factory, user_id)
    return user_id, {"Cookie": f"session={token}"}


async def rows(engine: AsyncEngine, sql: str) -> list[tuple[Any, ...]]:
    async with engine.connect() as connection:
        return [tuple(row) for row in await connection.execute(text(sql))]


async def discovery_jobs(engine: AsyncEngine) -> list[tuple[Any, ...]]:
    return await rows(
        engine, "SELECT dedupe_key, status FROM jobs WHERE kind = 'discover_filings' ORDER BY id"
    )


async def finish_checks(engine: AsyncEngine) -> None:
    """As if the worker had completed every queued check just now."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE jobs SET status = 'completed', updated_at = now() "
                "WHERE kind = 'discover_filings'"
            )
        )


async def age_checks(engine: AsyncEngine, minutes: int) -> None:
    """As if every check had been queued (and finished) this many minutes ago."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE jobs SET created_at = created_at - make_interval(mins => :m), "
                "updated_at = updated_at - make_interval(mins => :m)"
            ),
            {"m": minutes},
        )


def when(value: str) -> datetime:
    return datetime.fromisoformat(value)


# --- the status -----------------------------------------------------------------------------------


async def follow(client: httpx.AsyncClient, cookie: dict[str, str], symbol: str = "TCS") -> int:
    response = await client.put(f"/api/v1/stocks/{symbol}/follow", headers={**cookie, **ORIGIN})
    status: int = response.status_code
    return status


async def test_following_a_stock_checks_it_for_new_filings(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(switched_on(db_config)) as (_, client):
        assert await follow(client, cookie) == 204

    assert await discovery_jobs(admin_engine) == [("discover_filings:TCS", "pending")]


async def test_following_again_within_the_hour_does_not_check_again(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    _, other = await sign_in(make_user, session_factory)
    async with running_app(switched_on(db_config)) as (_, client):
        await follow(client, cookie)
        await finish_checks(admin_engine)
        await client.delete("/api/v1/stocks/TCS/follow", headers={**cookie, **ORIGIN})
        assert await follow(client, cookie) == 204  # the follow itself still works
        assert await follow(client, other) == 204  # per stock, not per person

    assert len(await discovery_jobs(admin_engine)) == 1


async def test_unfollowing_checks_nothing(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(switched_on(db_config)) as (_, client):
        response = await client.delete("/api/v1/stocks/TCS/follow", headers={**cookie, **ORIGIN})

    assert response.status_code == 204
    assert await discovery_jobs(admin_engine) == []


async def test_following_with_the_switch_off_only_follows(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings(filings_discovery=False)) as (_, client):
        assert await follow(client, cookie) == 204

    assert await discovery_jobs(admin_engine) == []
    assert await rows(admin_engine, "SELECT count(*) FROM user_follows") == [(1,)]


async def test_following_an_unknown_stock_queues_nothing(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(switched_on(db_config)) as (_, client):
        assert await follow(client, cookie, "INFY") == 404

    assert await discovery_jobs(admin_engine) == []


# --- the daily timer ------------------------------------------------------------------------------


async def test_the_daily_timer_skips_a_stock_checked_by_hand_today(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """A check is a check: the timer need not read the same page again the same day."""
    async with session_factory() as db:
        assert await enqueue_discovery(db, every_hours=CHECK_COOLDOWN_HOURS, symbol="TCS") == 1
        await db.commit()
    await finish_checks(admin_engine)

    async with session_factory() as db:
        assert await enqueue_discovery(db, every_hours=24) == 2
        await db.commit()

    pending = await rows(
        admin_engine,
        "SELECT dedupe_key FROM jobs WHERE status = 'pending' ORDER BY dedupe_key",
    )
    assert pending == [("discover_filings:HDFCBANK",), ("discover_filings:RELIANCE",)]
