"""Revision 0011: prices, price_days and the sync_prices job kind (ADR 025).

Every rule of the price history is a constraint, so a bug in app code cannot store a second row
for a stock and day, a zero price, or an unknown day status.
"""

from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import Migrator

pytestmark = pytest.mark.usefixtures("migrated_db")

PRICE = (
    "INSERT INTO prices (stock_id, trade_date, open, high, low, close, prev_close, volume) "
    "SELECT id, :day, :open, :high, :low, :close, :prev, :volume FROM stocks "
    "WHERE symbol = 'TCS'"
)


def price(**overrides: Any) -> dict[str, Any]:
    return {
        "day": "2026-09-28",
        "open": "10.50",
        "high": "11.00",
        "low": "10.00",
        "close": "10.75",
        "prev": "10.25",
        "volume": 1000,
        **overrides,
    }


async def add_price(engine: AsyncEngine, **overrides: Any) -> None:
    values = price(**overrides)
    values["day"] = date.fromisoformat(values["day"])
    async with engine.begin() as connection:
        await connection.execute(text(PRICE), values)


async def count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as connection:
        found: int = (
            await connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
        ).scalar_one()
        return found


@pytest.fixture(autouse=True)
async def empty_prices(admin_engine: AsyncEngine) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(text("TRUNCATE prices, price_days"))
        await connection.execute(text("DELETE FROM jobs WHERE kind = 'sync_prices'"))


async def test_a_well_formed_price_is_accepted_and_the_runtime_role_can_use_it(
    app_engine: AsyncEngine,
) -> None:
    await add_price(app_engine)
    async with app_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO price_days (trade_date, status) VALUES (DATE '2026-09-28', 'fetched') "
                "ON CONFLICT (trade_date) DO UPDATE SET attempts = price_days.attempts + 1"
            )
        )
    assert await count(app_engine, "prices") == 1
    assert await count(app_engine, "price_days") == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"open": "0"},
        {"high": "-1"},
        {"low": "0"},
        {"close": "0"},
        {"prev": "0"},
        {"volume": -1},
        {"close": None},
        {"volume": None},
    ],
    ids=["open", "high", "low", "close", "prev", "volume", "null-close", "null-volume"],
)
async def test_a_malformed_price_is_refused(
    admin_engine: AsyncEngine, overrides: dict[str, Any]
) -> None:
    with pytest.raises(DBAPIError):
        await add_price(admin_engine, **overrides)
    assert await count(admin_engine, "prices") == 0


async def test_zero_volume_is_allowed(admin_engine: AsyncEngine) -> None:
    await add_price(admin_engine, volume=0)
    assert await count(admin_engine, "prices") == 1


async def test_one_row_per_stock_and_day(admin_engine: AsyncEngine) -> None:
    await add_price(admin_engine)
    with pytest.raises(IntegrityError):
        await add_price(admin_engine, close="99.00")
    await add_price(admin_engine, day="2026-09-25")
    assert await count(admin_engine, "prices") == 2


async def test_a_price_needs_a_real_stock(admin_engine: AsyncEngine) -> None:
    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO prices (stock_id, trade_date, open, high, low, close, "
                    "prev_close, volume) VALUES (-1, DATE '2026-09-28', 1, 1, 1, 1, 1, 1)"
                )
            )


@pytest.mark.parametrize(
    "values",
    [
        "(DATE '2026-09-28', 'exploded', 0)",
        "(DATE '2026-09-28', 'missing', -1)",
        "(NULL, 'missing', 0)",
    ],
    ids=["unknown-status", "negative-attempts", "no-date"],
)
async def test_a_malformed_day_is_refused(admin_engine: AsyncEngine, values: str) -> None:
    with pytest.raises(DBAPIError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    f"INSERT INTO price_days (trade_date, status, attempts) VALUES {values}"  # noqa: S608
                )
            )
    assert await count(admin_engine, "price_days") == 0


@pytest.mark.parametrize("status", ["fetched", "missing", "no_file"])
async def test_the_three_day_statuses_are_allowed_and_attempts_start_at_zero(
    admin_engine: AsyncEngine, status: str
) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO price_days (trade_date, status) VALUES (DATE '2026-09-28', :s)"),
            {"s": status},
        )
        attempts = (await connection.execute(text("SELECT attempts FROM price_days"))).scalar_one()
    assert attempts == 0


async def test_the_sync_prices_job_kind_is_allowed(admin_engine: AsyncEngine) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('sync_prices', '{}', 'k')")
        )
    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('bogus', '{}', 'j')")
            )


async def test_downgrading_removes_the_tables_and_the_job_kind(
    migrator: Migrator, admin_engine: AsyncEngine
) -> None:
    await add_price(admin_engine)
    async with admin_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('sync_prices', '{}', 'k')")
        )

    await migrator.downgrade("0010")
    try:
        async with admin_engine.connect() as connection:
            tables = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                        "AND tablename IN ('prices', 'price_days')"
                    )
                )
            ).scalar_one()
            jobs = (
                await connection.execute(
                    text("SELECT count(*) FROM jobs WHERE kind = 'sync_prices'")
                )
            ).scalar_one()
        assert (tables, jobs) == (0, 0)
    finally:
        await migrator.upgrade("head")
