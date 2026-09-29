"""Storing prices (ADR 025): plain SQL, one short transaction owned by the caller.

Nothing here commits and nothing here talks to the network. Idempotency is the database's job:
``prices`` has PRIMARY KEY (stock_id, trade_date), so ``ON CONFLICT DO NOTHING`` stores a day once
however many runs, workers or retries see it. ``price_days`` remembers which days are done.
"""

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.prices.model import DailyPrice

STRIKES = 3  # "missing" this many times and the day is taken to have no file (a holiday)

_INSERT = text(
    """
    INSERT INTO prices (stock_id, trade_date, open, high, low, close, prev_close, volume)
    SELECT id, :day, :open, :high, :low, :close, :prev_close, :volume
    FROM stocks WHERE bse_code = :code
    ON CONFLICT (stock_id, trade_date) DO NOTHING
    """
)

_MARK_FETCHED = text(
    """
    INSERT INTO price_days (trade_date, status) VALUES (:day, 'fetched')
    ON CONFLICT (trade_date) DO UPDATE SET status = 'fetched', updated_at = now()
    """
)

# The first strike inserts attempts = 1; later strikes add one, atomically, so concurrent runs
# cannot lose a count. A day already fetched is never demoted.
_MARK_MISSING = text(
    """
    INSERT INTO price_days (trade_date, status, attempts) VALUES (:day, 'missing', 1)
    ON CONFLICT (trade_date) DO UPDATE SET
        attempts = price_days.attempts + 1,
        status = CASE WHEN price_days.attempts + 1 >= :strikes THEN 'no_file' ELSE 'missing' END,
        updated_at = now()
    WHERE price_days.status <> 'fetched'
    """
)

_DAY_STATUS = text("SELECT status FROM price_days WHERE trade_date = :day")

_TO_FETCH = text(
    """
    SELECT d::date FROM generate_series(CAST(:first AS date), CAST(:last AS date),
                                        interval '1 day') AS d
    WHERE extract(isodow FROM d) < 6
      AND NOT EXISTS (
          SELECT 1 FROM price_days p
          WHERE p.trade_date = d::date AND p.status IN ('fetched', 'no_file')
      )
    ORDER BY d DESC
    """
)

_LOAD = text(
    """
    SELECT s.bse_code, p.trade_date, p.open, p.high, p.low, p.close, p.prev_close, p.volume
    FROM prices p JOIN stocks s ON s.id = p.stock_id
    WHERE p.stock_id = :stock_id
    ORDER BY p.trade_date
    """
)


async def add_prices(db: AsyncSession, rows: Sequence[DailyPrice]) -> int:
    """Store the rows not stored yet; returns how many were new. A code that is no stock of ours
    matches no stock row and stores nothing."""
    added = 0
    for row in rows:
        result = cast(
            "CursorResult[Any]",
            await db.execute(
                _INSERT,
                {
                    "code": row.bse_code,
                    "day": row.trade_date,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "prev_close": row.prev_close,
                    "volume": row.volume,
                },
            ),
        )
        added += result.rowcount
    return added


async def mark_day(db: AsyncSession, day: date, status: Literal["fetched", "missing"]) -> str:
    """Record the outcome for a day and return the day's status now: 'fetched', 'missing', or
    'no_file' once the third 'missing' strike is in. A fetched day stays fetched."""
    if status == "fetched":
        await db.execute(_MARK_FETCHED, {"day": day})
    else:
        await db.execute(_MARK_MISSING, {"day": day, "strikes": STRIKES})
    return str((await db.execute(_DAY_STATUS, {"day": day})).scalar_one())


_COOLING = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM price_days
        WHERE status IN ('missing', 'no_file')
          AND updated_at > now() - make_interval(mins => :minutes)
    )
    """
)


async def cooling_down(db: AsyncSession, *, minutes: int) -> bool:
    """True while BSE's last "slow down" (a strike on any day) is more recent than ``minutes``:
    no request until it has passed, so strikes only count when they are far apart."""
    return bool((await db.execute(_COOLING, {"minutes": minutes})).scalar_one())


async def days_to_fetch(db: AsyncSession, *, today: date, history_days: int) -> list[date]:
    """Weekdays from ``history_days`` ago to YESTERDAY that are not fetched and not no_file,
    newest first. Today is left out: BSE publishes a day's file only after the close, and a
    strike earned before it exists must not turn a real trading day into a "holiday"."""
    result = await db.execute(
        _TO_FETCH,
        {"first": today - timedelta(days=history_days), "last": today - timedelta(days=1)},
    )
    return [row[0] for row in result]


async def load_prices(db: AsyncSession, stock_id: int) -> list[DailyPrice]:
    """Every stored price of one stock, oldest first."""
    result = await db.execute(_LOAD, {"stock_id": stock_id})
    return [
        DailyPrice(
            bse_code=str(code),
            trade_date=day,
            open=Decimal(open_),
            high=Decimal(high),
            low=Decimal(low),
            close=Decimal(close),
            prev_close=Decimal(prev),
            volume=int(volume),
        )
        for code, day, open_, high, low, close, prev, volume in result
    ]
