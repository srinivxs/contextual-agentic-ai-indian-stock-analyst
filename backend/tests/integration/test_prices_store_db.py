"""Storing prices against the real database (ADR 025): idempotency comes from the constraints."""

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.prices.model import DailyPrice
from app.prices.store import add_prices, days_to_fetch, load_prices, mark_day
from tests.integration.price_helpers import DEMO_A, DEMO_B, demo_stocks, make_price

pytestmark = pytest.mark.usefixtures("migrated_db", "demo_stocks")
__all__ = ["demo_stocks"]

Factory = async_sessionmaker[AsyncSession]

MON = date(2026, 9, 28)  # a Monday
FRI = date(2026, 9, 25)


async def add(factory: Factory, rows: list[DailyPrice]) -> int:
    async with factory() as db:
        added = await add_prices(db, rows)
        await db.commit()
    return added


async def count(engine: AsyncEngine, table: str = "prices") -> int:
    async with engine.connect() as connection:
        found: int = (
            await connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
        ).scalar_one()
        return found


# --- add_prices ---------------------------------------------------------------------------------


async def test_prices_are_stored_and_loaded_oldest_first(
    session_factory: Factory, demo_stocks: dict[str, int]
) -> None:
    rows = [make_price(day=MON, close="102.00"), make_price(day=FRI, close="101.00")]
    assert await add(session_factory, rows) == 2
    async with session_factory() as db:
        loaded = await load_prices(db, demo_stocks[DEMO_A])
    assert [p.trade_date for p in loaded] == [FRI, MON]
    assert loaded[1] == make_price(day=MON, close="102.00")
    assert isinstance(loaded[0].close, Decimal)


async def test_repeating_a_day_stores_nothing_new_and_keeps_the_first_row(
    session_factory: Factory, admin_engine: AsyncEngine, demo_stocks: dict[str, int]
) -> None:
    await add(session_factory, [make_price(close="100.00")])
    assert await add(session_factory, [make_price(close="555.00")]) == 0
    assert await count(admin_engine) == 1
    async with session_factory() as db:
        [only] = await load_prices(db, demo_stocks[DEMO_A])
    assert only.close == Decimal("100.00")


async def test_a_code_that_is_no_stock_of_ours_is_ignored(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    assert await add(session_factory, [make_price(code="999999")]) == 0
    assert await count(admin_engine) == 0


async def test_no_rows_is_fine(session_factory: Factory) -> None:
    assert await add(session_factory, []) == 0


async def test_concurrent_adds_of_the_same_rows_leave_one_row_per_stock_and_day(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    rows = [make_price(DEMO_A), make_price(DEMO_B), make_price(DEMO_A, FRI)]
    await asyncio.gather(*(add(session_factory, rows) for _ in range(8)))
    assert await count(admin_engine) == 3


async def test_prices_belong_to_their_own_stock(
    session_factory: Factory, demo_stocks: dict[str, int]
) -> None:
    await add(
        session_factory, [make_price(DEMO_A, close="10.00"), make_price(DEMO_B, close="20.00")]
    )
    async with session_factory() as db:
        [a] = await load_prices(db, demo_stocks[DEMO_A])
        [b] = await load_prices(db, demo_stocks[DEMO_B])
        assert await load_prices(db, -1) == []
    assert (a.bse_code, a.close, b.bse_code, b.close) == (
        DEMO_A,
        Decimal("10.00"),
        DEMO_B,
        Decimal("20.00"),
    )


# --- mark_day and the three strikes -------------------------------------------------------------


async def day_row(engine: AsyncEngine, day: date) -> tuple[str, int] | None:
    async with engine.connect() as connection:
        found = (
            await connection.execute(
                text("SELECT status, attempts FROM price_days WHERE trade_date = :d"), {"d": day}
            )
        ).one_or_none()
    return None if found is None else (found[0], found[1])


async def mark(factory: Factory, day: date, status: str) -> str:
    async with factory() as db:
        result = await mark_day(db, day, status)  # type: ignore[arg-type]
        await db.commit()
    return result


async def test_a_fetched_day_is_remembered(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    assert await mark(session_factory, MON, "fetched") == "fetched"
    assert await day_row(admin_engine, MON) == ("fetched", 0)
    await mark(session_factory, MON, "fetched")  # repeating is harmless
    assert await day_row(admin_engine, MON) == ("fetched", 0)


async def test_three_strikes_make_a_day_no_file(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    assert await mark(session_factory, MON, "missing") == "missing"
    assert await day_row(admin_engine, MON) == ("missing", 1)
    assert await mark(session_factory, MON, "missing") == "missing"
    assert await day_row(admin_engine, MON) == ("missing", 2)
    assert await mark(session_factory, MON, "missing") == "no_file"
    assert await day_row(admin_engine, MON) == ("no_file", 3)


async def test_a_fetched_day_is_never_demoted_by_a_late_missing(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await mark(session_factory, MON, "fetched")
    assert await mark(session_factory, MON, "missing") == "fetched"
    assert await day_row(admin_engine, MON) == ("fetched", 0)


async def test_a_missing_day_that_is_then_fetched_becomes_fetched(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await mark(session_factory, MON, "missing")
    await mark(session_factory, MON, "fetched")
    assert await day_row(admin_engine, MON) == ("fetched", 1)


async def test_concurrent_strikes_are_all_counted(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await asyncio.gather(*(mark(session_factory, MON, "missing") for _ in range(5)))
    row = await day_row(admin_engine, MON)
    assert row is not None
    assert row[1] == 5
    assert row[0] == "no_file"


# --- days_to_fetch ------------------------------------------------------------------------------


async def todo(factory: Factory, today: date, history_days: int) -> list[date]:
    async with factory() as db:
        return await days_to_fetch(db, today=today, history_days=history_days)


async def test_a_declared_bse_holiday_is_never_asked_for(session_factory: Factory) -> None:
    # Tue 6 Oct 2026, 7 days back: Mon 5, (Fri 2 = Gandhi Jayanti), Thu 1, Wed 30 Sep, Tue 29 Sep
    days = await todo(session_factory, date(2026, 10, 6), 7)
    assert days == [date(2026, 10, 5), date(2026, 10, 1), date(2026, 9, 30), date(2026, 9, 29)]


async def test_weekdays_come_newest_first_and_today_is_left_for_tomorrow(
    session_factory: Factory,
) -> None:
    # Tue 29 Sep 2026: yesterday back 7 days = Tue 22 .. Mon 28; the weekend of 26-27 is skipped
    days = await todo(session_factory, date(2026, 9, 29), 7)
    assert days == [
        date(2026, 9, 28),
        date(2026, 9, 25),
        date(2026, 9, 24),
        date(2026, 9, 23),
        date(2026, 9, 22),
    ]


async def test_fetched_and_no_file_days_are_skipped_but_missing_days_are_retried(
    session_factory: Factory,
) -> None:
    await mark(session_factory, date(2026, 9, 28), "fetched")
    for _ in range(3):
        await mark(session_factory, date(2026, 9, 25), "missing")  # ends as no_file
    await mark(session_factory, date(2026, 9, 24), "missing")  # one strike only
    days = await todo(session_factory, date(2026, 9, 29), 5)
    assert days == [date(2026, 9, 24)]  # the window is 24 to 28 September


async def test_nothing_is_left_when_everything_is_done(session_factory: Factory) -> None:
    for day in (date(2026, 9, 28), date(2026, 9, 25)):
        await mark(session_factory, day, "fetched")
    assert await todo(session_factory, date(2026, 9, 29), 3) == []
