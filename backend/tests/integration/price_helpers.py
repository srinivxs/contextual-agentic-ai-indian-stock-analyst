"""Shared pieces of the price tests: two fictional stocks (BSE codes 999901 and 999902), which
exist only while a test runs, and a builder for synthetic prices."""

from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.prices.model import DailyPrice

DEMO_A = "999901"
DEMO_B = "999902"


def make_price(
    code: str = DEMO_A, day: date = date(2026, 9, 28), close: str = "100.00", **overrides: object
) -> DailyPrice:
    values: dict[str, object] = {
        "bse_code": code,
        "trade_date": day,
        "open": Decimal("99.00"),
        "high": Decimal("101.00"),
        "low": Decimal("98.00"),
        "close": Decimal(close),
        "prev_close": Decimal("99.50"),
        "volume": 1000,
        **overrides,
    }
    return DailyPrice(**values)  # type: ignore[arg-type]


@pytest.fixture
async def demo_stocks(admin_engine: AsyncEngine) -> AsyncIterator[dict[str, int]]:
    """{bse_code: stock id} of two fictional stocks, removed again with their prices."""
    async with admin_engine.begin() as connection:
        await connection.execute(text("TRUNCATE prices, price_days"))
        await connection.execute(text("DELETE FROM jobs WHERE kind = 'sync_prices'"))
        rows = await connection.execute(
            text(
                "INSERT INTO stocks (symbol, name, bse_code, sector) VALUES "
                "('DEMOA', 'DemoCo A', :a, 'Demo'), ('DEMOB', 'DemoCo B', :b, 'Demo') "
                "RETURNING bse_code, id"
            ),
            {"a": DEMO_A, "b": DEMO_B},
        )
        ids = {str(code): int(stock_id) for code, stock_id in rows}
    try:
        yield ids
    finally:
        async with admin_engine.begin() as connection:
            await connection.execute(text("TRUNCATE prices, price_days"))
            await connection.execute(text("DELETE FROM jobs WHERE kind = 'sync_prices'"))
            await connection.execute(
                text("DELETE FROM stocks WHERE bse_code IN (:a, :b)"), {"a": DEMO_A, "b": DEMO_B}
            )
