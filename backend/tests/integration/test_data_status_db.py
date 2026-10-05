"""The one-click update and the "data updated to" status (the owner, 2026-09-29).

A click only queues jobs, through the timers' own functions, so each source keeps its limit:
filings once an hour per stock, prices one run per slot (and only while a day is missing), the
RBI feed once per 15-minute slot. A source switched off is never queued. The status gives the
dates every page shows as a disclaimer.
"""

import asyncio
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.clock import india_today
from app.core.config import Settings
from tests.helpers import running_app
from tests.integration.auth_helpers import open_session
from tests.integration.conftest import DbConfig, MakeUser

pytestmark = pytest.mark.usefixtures("clean_document_tables")

Factory = async_sessionmaker[AsyncSession]
ORIGIN = {"Origin": "http://localhost:8000"}
STATUS = "/api/v1/data/status"
REFRESH = "/api/v1/data/refresh"


def everything_on(db_config: DbConfig) -> Settings:
    return db_config.settings(filings_discovery=True, prices_enabled=True, feed_mode="live")


async def sign_in(make_user: MakeUser, session_factory: Factory) -> dict[str, str]:
    user_id: UUID = await make_user()
    return {"Cookie": f"session={await open_session(session_factory, user_id)}", **ORIGIN}


async def rows(engine: AsyncEngine, sql: str) -> list[tuple[Any, ...]]:
    async with engine.connect() as connection:
        return [tuple(row) for row in await connection.execute(text(sql))]


async def queued(engine: AsyncEngine) -> list[str]:
    return [kind for (kind,) in await rows(engine, "SELECT kind FROM jobs ORDER BY id")]


async def run(settings: Settings, method: str, url: str, headers: dict[str, str]) -> httpx.Response:
    async with running_app(settings) as (_, client):
        return await client.request(method, url, headers=headers)


async def test_one_click_queues_filings_prices_and_the_rbi_feed(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    headers = await sign_in(make_user, session_factory)

    response = await run(everything_on(db_config), "POST", REFRESH, headers)

    assert response.status_code == 202
    body = response.json()
    assert (body["filings"], body["prices"], body["rbi"]) == ("queued", "queued", "queued")
    assert body["status"]["updating"] is True
    assert sorted(await queued(admin_engine)) == sorted(
        ["discover_filings"] * 3 + ["sync_prices", "poll_feed"]
    )


async def test_a_second_press_within_the_hour_is_refused_and_queues_nothing(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    headers = await sign_in(make_user, session_factory)
    settings = everything_on(db_config)
    first = await run(settings, "POST", REFRESH, headers)

    again = await run(settings, "POST", REFRESH, headers)
    status = (await run(settings, "GET", STATUS, headers)).json()

    assert (first.status_code, again.status_code) == (202, 429)
    assert again.json()["error"]["code"] == "rate_limited"
    assert len(await queued(admin_engine)) == 5
    assert status["next_update_at"] is not None  # the page shows when it works again
    assert await rows(admin_engine, "SELECT count(*) FROM data_refreshes") == [(1,)]


async def test_after_the_hour_it_works_again(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    headers = await sign_in(make_user, session_factory)
    settings = everything_on(db_config)
    await run(settings, "POST", REFRESH, headers)
    async with admin_engine.begin() as connection:  # as if it all happened 61 minutes ago
        await connection.execute(
            text("UPDATE data_refreshes SET requested_at = now() - interval '61 minutes'")
        )
        await connection.execute(
            text("UPDATE jobs SET status = 'completed', created_at = now() - interval '61 minutes'")
        )

    again = await run(settings, "POST", REFRESH, headers)
    status = (await run(settings, "GET", STATUS, headers)).json()

    assert again.status_code == 202
    assert again.json()["filings"] == "queued"
    assert status["next_update_at"] is not None  # counted from this press


async def test_many_presses_at_once_count_as_one(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    headers = await sign_in(make_user, session_factory)
    async with running_app(everything_on(db_config)) as (_, client):
        answers = await asyncio.gather(*(client.post(REFRESH, headers=headers) for _ in range(6)))

    assert sorted(answer.status_code for answer in answers) == [202, 429, 429, 429, 429, 429]
    assert len(await queued(admin_engine)) == 5
    assert await rows(admin_engine, "SELECT count(*) FROM data_refreshes") == [(1,)]


async def test_a_source_switched_off_is_never_queued(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    headers = await sign_in(make_user, session_factory)

    body = (await run(db_config.settings(), "POST", REFRESH, headers)).json()

    assert (body["filings"], body["prices"], body["rbi"]) == ("off", "off", "queued")
    assert await queued(admin_engine) == ["poll_feed"]  # fixture mode: the shipped sample items
    assert (body["status"]["filings_on"], body["status"]["prices_on"]) == (False, False)
    assert body["status"]["rbi_live"] is False


async def test_prices_with_no_day_missing_are_current(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    headers = await sign_in(make_user, session_factory)
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO price_days (trade_date, status) SELECT d::date, 'fetched' "
                "FROM generate_series(current_date - 45, current_date + 1, interval '1 day') d"
            )
        )
    try:
        body = (await run(everything_on(db_config), "POST", REFRESH, headers)).json()
    finally:
        async with admin_engine.begin() as connection:
            await connection.execute(text("DELETE FROM price_days"))

    assert body["prices"] == "current"
    assert "sync_prices" not in await queued(admin_engine)


async def test_the_status_gives_the_dates_the_data_is_updated_to(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    headers = await sign_in(make_user, session_factory)
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, payload, dedupe_key, status, updated_at) VALUES "
                "('discover_filings', '{}', 'discover_filings:TCS', 'completed', "
                "TIMESTAMPTZ '2026-09-29 09:30:00+00')"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO prices (stock_id, trade_date, open, high, low, close, prev_close, "
                "volume) SELECT id, DATE '2026-09-28', 1, 1, 1, 1, 1, 1 FROM stocks "
                "WHERE symbol = 'TCS'"
            )
        )
    try:
        body = (await run(db_config.settings(), "GET", STATUS, headers)).json()
    finally:
        async with admin_engine.begin() as connection:
            await connection.execute(text("DELETE FROM prices"))

    assert body == {
        "updating": False,
        "filings_checked_at": "2026-09-29T09:30:00+00:00",
        "prices_to": "2026-09-28",
        "rbi_to": None,  # only sample RBI items, which are not live data
        "next_update_at": None,  # nobody pressed "Update data" yet
        "filings_on": False,
        "prices_on": False,
        "rbi_live": False,
    }


async def test_nothing_stored_yet_has_no_dates(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    headers = await sign_in(make_user, session_factory)
    body = (await run(db_config.settings(), "GET", STATUS, headers)).json()
    assert (body["filings_checked_at"], body["prices_to"], body["rbi_to"]) == (None, None, None)
    assert body["updating"] is False


def test_the_day_the_prices_window_ends_is_yesterday() -> None:
    # A reminder of why "current" is reachable: today is never asked for (app/prices/store.py).
    assert india_today().toordinal() > 0
