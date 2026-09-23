"""Checking a stock for new filings on demand: on a follow, or with the "Check for new filings"
button.

Both only queue a ``discover_filings`` job for that one stock; the worker does the rest, and fetches
only filings not already stored (tests/integration/test_filings_db.py). What is checked here: at
most one check per stock per hour (CHECK_COOLDOWN_HOURS), one job however many people click at
once, nothing at all when the switch is off, and the status the Documents page shows.
"""

import asyncio
from datetime import datetime, timedelta
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


async def test_a_stock_never_checked_says_so(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(switched_on(db_config)) as (_, client):
        response = await client.get(check_url(), headers=cookie)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "enabled": True,
        "checking": False,
        "last_checked_at": None,
        "next_check_at": None,
    }


async def test_the_status_says_when_checks_are_switched_off(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings(filings_discovery=False)) as (_, client):
        response = await client.get(check_url(), headers=cookie)
    assert response.json()["enabled"] is False


async def test_a_fetch_still_under_way_counts_as_checking_for_its_own_stock_only(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """The check itself ends once the links are read; the downloads it queued can take minutes.
    The page must keep saying "Checking" until the last of them is done."""
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('fetch_filing', "
                "jsonb_build_object('symbol', 'TCS'), 'fetch_filing:demo')"
            )
        )
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(switched_on(db_config)) as (_, client):
        tcs = (await client.get(check_url("TCS"), headers=cookie)).json()
        reliance = (await client.get(check_url("RELIANCE"), headers=cookie)).json()

    assert tcs["checking"] is True
    assert reliance["checking"] is False


# --- the button -----------------------------------------------------------------------------------


async def test_checking_queues_one_discovery_for_that_stock_only(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(switched_on(db_config)) as (_, client):
        response = await client.post(check_url(), headers={**cookie, **ORIGIN})

    assert response.status_code == 202
    body = response.json()
    assert body["checking"] is True
    assert body["last_checked_at"] is None
    [(created,)] = await rows(admin_engine, "SELECT created_at FROM jobs")
    assert when(body["next_check_at"]) == created + timedelta(hours=CHECK_COOLDOWN_HOURS)
    assert await discovery_jobs(admin_engine) == [("discover_filings:TCS", "pending")]


async def test_within_the_hour_a_finished_check_is_not_repeated(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(switched_on(db_config)) as (_, client):
        await client.post(check_url(), headers={**cookie, **ORIGIN})
        await finish_checks(admin_engine)
        await age_checks(admin_engine, minutes=59)
        again = await client.post(check_url(), headers={**cookie, **ORIGIN})
        status = (await client.get(check_url(), headers=cookie)).json()

    assert again.status_code == 429
    assert again.json()["error"]["code"] == "rate_limited"
    assert status["checking"] is False
    assert status["last_checked_at"] is not None
    assert when(status["next_check_at"]) > datetime.now().astimezone()
    assert len(await discovery_jobs(admin_engine)) == 1


async def test_after_the_hour_the_stock_can_be_checked_again(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(switched_on(db_config)) as (_, client):
        await client.post(check_url(), headers={**cookie, **ORIGIN})
        await finish_checks(admin_engine)
        await age_checks(admin_engine, minutes=61)
        before = (await client.get(check_url(), headers=cookie)).json()
        again = await client.post(check_url(), headers={**cookie, **ORIGIN})

    assert before["next_check_at"] is None  # the button is enabled
    assert again.status_code == 202
    assert await discovery_jobs(admin_engine) == [
        ("discover_filings:TCS", "completed"),
        ("discover_filings:TCS", "pending"),
    ]


async def test_asking_while_a_check_is_under_way_is_fine_and_queues_nothing_more(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(switched_on(db_config)) as (_, client):
        first = await client.post(check_url(), headers={**cookie, **ORIGIN})
        second = await client.post(check_url(), headers={**cookie, **ORIGIN})

    assert (first.status_code, second.status_code) == (202, 202)
    assert len(await discovery_jobs(admin_engine)) == 1


async def test_eight_people_clicking_at_once_queue_one_check(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """Two transactions can both see "no recent check"; the partial unique index on the live
    dedupe key then lets only one insert through, and ON CONFLICT turns the rest into nothing."""
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(switched_on(db_config)) as (_, client):
        responses = await asyncio.gather(
            *(client.post(check_url(), headers={**cookie, **ORIGIN}) for _ in range(8))
        )

    assert [r.status_code for r in responses] == [202] * 8
    assert await discovery_jobs(admin_engine) == [("discover_filings:TCS", "pending")]


async def test_with_the_switch_off_nothing_is_queued(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """The worker could not run it (it has no internet client when switched off), so a queued job
    would only fail. The api refuses instead."""
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings(filings_discovery=False)) as (_, client):
        response = await client.post(check_url(), headers={**cookie, **ORIGIN})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert await discovery_jobs(admin_engine) == []


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_an_unknown_stock_is_404(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    method: str,
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(switched_on(db_config)) as (_, client):
        response = await client.request(method, check_url("INFY"), headers={**cookie, **ORIGIN})

    assert response.status_code == 404
    assert "INFY" not in response.text
    assert await discovery_jobs(admin_engine) == []


async def test_an_invalid_symbol_is_422(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(switched_on(db_config)) as (_, client):
        response = await client.post(check_url("tcs"), headers={**cookie, **ORIGIN})
    assert response.status_code == 422


# --- following ------------------------------------------------------------------------------------


async def follow(client: httpx.AsyncClient, cookie: dict[str, str], symbol: str = "TCS") -> int:
    response = await client.put(f"/api/v1/stocks/{symbol}/follow", headers={**cookie, **ORIGIN})
    return response.status_code


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
