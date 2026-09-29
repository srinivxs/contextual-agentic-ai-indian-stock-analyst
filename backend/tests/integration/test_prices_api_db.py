"""GET /api/v1/stocks/{symbol}/prices over HTTP, against the real database (ADR 025).

The fictional stock DEMOA (BSE code 999901) carries synthetic prices and facts; nothing here is
tied to a real company.
"""

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.helpers import running_app
from tests.integration.auth_helpers import open_session
from tests.integration.conftest import DbConfig, MakeUser
from tests.integration.price_helpers import DEMO_A, demo_stocks

pytestmark = pytest.mark.usefixtures(
    "clean_document_tables", "forget_stale_demo_stocks", "demo_stocks", "forget_demo_facts"
)
__all__ = ["demo_stocks"]

Factory = async_sessionmaker[AsyncSession]
SCREENER = "https://www.screener.in/company/DEMOA/consolidated/"
LAST_DAY = date(2026, 9, 28)
SOURCE = "BSE daily price file (end of day, not live)"


_DELETE_DEMO_FACTS = text(
    "DELETE FROM facts WHERE stock_id IN (SELECT id FROM stocks WHERE bse_code = :code)"
)


@pytest.fixture
async def forget_stale_demo_stocks(admin_engine: AsyncEngine) -> None:
    """Runs before demo_stocks: a demo stock left by an earlier failed run would collide."""
    async with admin_engine.begin() as connection:
        await connection.execute(_DELETE_DEMO_FACTS, {"code": DEMO_A})
        await connection.execute(
            text("DELETE FROM stocks WHERE bse_code = :code"), {"code": DEMO_A}
        )


@pytest.fixture
async def forget_demo_facts(admin_engine: AsyncEngine) -> AsyncIterator[None]:
    """Runs before demo_stocks is removed: facts reference the stock, so they go first."""
    yield
    async with admin_engine.begin() as connection:
        await connection.execute(_DELETE_DEMO_FACTS, {"code": DEMO_A})


async def sign_in(make_user: MakeUser, session_factory: Factory) -> tuple[UUID, dict[str, str]]:
    user_id = await make_user()
    token = await open_session(session_factory, user_id)
    return user_id, {"Cookie": f"session={token}"}


async def add_prices(
    engine: AsyncEngine, stock_id: int, rows: list[tuple[date, Decimal, Decimal]]
) -> None:
    """rows: (day, close, prev_close)."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO prices (stock_id, trade_date, open, high, low, close, prev_close, "
                "volume) VALUES (:stock, :day, :close, :close, :close, :close, :prev, 10)"
            ),
            [{"stock": stock_id, "day": d, "close": c, "prev": p} for d, c, p in rows],
        )


def ramp(days: int, first: str, last: str) -> list[tuple[date, Decimal, Decimal]]:
    """One row per calendar day ending on LAST_DAY, closes rising in a straight line."""
    low, high = Decimal(first), Decimal(last)
    closes = [
        (low + (high - low) * index / (days - 1)).quantize(Decimal("0.01")) for index in range(days)
    ]
    return [
        (LAST_DAY - timedelta(days=days - 1 - index), close, closes[index - 1] if index else close)
        for index, close in enumerate(closes)
    ]


async def screener_fact(
    engine: AsyncEngine, metric: str, value: str, period: str = "FY2026"
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO facts (stock_id, source, source_url, source_section, source_row, "
                "source_column, metric, period, period_end, basis, currency, unit, value) "
                "SELECT id, 'screener', :url, 'ratios', :metric, :period, :metric, :period, "
                "make_date(2026, 3, 31), 'consolidated', 'INR', 'INR_PER_SHARE', :value "
                "FROM stocks WHERE symbol = 'DEMOA'"
            ),
            {"url": SCREENER, "metric": metric, "period": period, "value": Decimal(value)},
        )


async def get_prices(db_config: DbConfig, cookie: dict[str, str], symbol: str = "DEMOA") -> Any:
    async with running_app(db_config.settings()) as (_, client):
        return await client.get(f"/api/v1/stocks/{symbol}/prices", headers=cookie)


async def test_prices_with_history_returns_pe_yield_and_citations(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    demo_stocks: dict[str, int],
) -> None:
    rows = ramp(400, "100", "200")
    await add_prices(admin_engine, demo_stocks[DEMO_A], rows)
    await screener_fact(admin_engine, "eps_basic", "8")
    await screener_fact(admin_engine, "dividend_per_share", "5")
    _, cookie = await sign_in(make_user, session_factory)

    response = await get_prices(db_config, cookie)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert list(body) == [
        "symbol",
        "source",
        "latest",
        "history",
        "returns",
        "volatility_1y",
        "pe",
        "dividend_yield",
        "actions",
    ]
    assert (body["symbol"], body["source"]) == ("DEMOA", SOURCE)
    latest = body["latest"]
    assert latest["date"] == "2026-09-28"
    assert latest["close"] == "200.00"
    assert latest["prev_close"] == str(rows[-2][1])
    assert latest["change_pct"] == "0.1"
    assert latest["citation"] == {
        "source": "filing",
        "label": "BSE daily price file · 28 Sep 2026",
        "url": "https://www.bseindia.com/download/BhavCopy/Equity/"
        "BhavCopy_BSE_CM_0_0_0_20260928_F_0000.CSV",
        "quote": None,
    }
    history = body["history"]
    assert (history[0]["date"], history[-1]["date"]) == ("2025-09-28", "2026-09-28")
    assert len(history) == 366  # the last 365 days, both ends included
    assert history[-1] == {"date": "2026-09-28", "close": "200.00"}
    assert set(body["returns"]) == {"1m", "3m", "6m", "1y"}
    assert body["returns"] == {"1m": "4.0", "3m": "13.0", "6m": "30.0", "1y": "84.3"}
    assert Decimal(body["volatility_1y"]) < 5
    assert body["actions"] == []
    pe = body["pe"]
    assert (pe["status"], pe["value"]) == ("ok", "25.0")
    assert pe["reason"].startswith("Share price ₹200 (28 Sep 2026) divided by basic EPS of ₹8")
    assert [c["source"] for c in pe["citations"]] == ["filing", "screener"]
    assert pe["citations"][1]["label"] == "screener.in · ratios · eps_basic · FY2026"
    assert body["dividend_yield"]["status"] == "ok"
    assert body["dividend_yield"]["value"] == "2.5"
    assert len(body["dividend_yield"]["citations"]) == 2


async def test_a_bonus_is_an_action_and_the_history_is_adjusted(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    demo_stocks: dict[str, int],
) -> None:
    first = date(2026, 6, 1)
    rows = [(first + timedelta(days=i), Decimal("100"), Decimal("100")) for i in range(50)]
    ex_date = first + timedelta(days=50)  # 21 Jul 2026, after FY2026 ended
    rows += [(ex_date + timedelta(days=i), Decimal("50"), Decimal("50")) for i in range(10)]
    await add_prices(admin_engine, demo_stocks[DEMO_A], rows)
    await screener_fact(admin_engine, "eps_basic", "8")
    _, cookie = await sign_in(make_user, session_factory)

    body = (await get_prices(db_config, cookie)).json()

    assert body["actions"] == [{"date": "2026-07-21", "factor": "0.5"}]
    assert {point["close"] for point in body["history"]} == {"50.00"}
    assert body["latest"]["change_pct"] == "0.0"
    assert body["pe"]["status"] == "not_assessable"
    assert body["pe"]["value"] is None
    assert "bonus issue or split on 21 Jul 2026" in body["pe"]["reason"]
    assert len(body["pe"]["citations"]) == 2
    assert body["volatility_1y"] is None  # too little history
    assert body["returns"] == {"1m": "0.0", "3m": None, "6m": None, "1y": None}


async def test_a_stock_without_prices_says_so(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, cookie = await sign_in(make_user, session_factory)

    body = (await get_prices(db_config, cookie)).json()

    assert body["latest"] is None
    assert body["history"] == []
    assert body["returns"] == {"1m": None, "3m": None, "6m": None, "1y": None}
    assert body["volatility_1y"] is None
    assert body["actions"] == []
    for name in ("pe", "dividend_yield"):
        assert body[name] == {
            "status": "not_assessable",
            "value": None,
            "reason": "Not assessable: no share price is in the data.",
            "citations": [],
        }


async def test_prices_without_eps_or_dividend_are_not_assessable(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    demo_stocks: dict[str, int],
) -> None:
    await add_prices(admin_engine, demo_stocks[DEMO_A], ramp(3, "10", "12"))
    _, cookie = await sign_in(make_user, session_factory)

    body = (await get_prices(db_config, cookie)).json()

    assert body["latest"]["close"] == "12.00"
    assert body["pe"]["status"] == "not_assessable"
    assert body["pe"]["reason"] == "Not assessable: no yearly basic EPS is on record."
    assert len(body["pe"]["citations"]) == 1  # the price it would have used
    assert "no yearly dividend" in body["dividend_yield"]["reason"]


async def test_an_unknown_stock_is_404_without_repeating_the_symbol(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, cookie = await sign_in(make_user, session_factory)

    response = await get_prices(db_config, cookie, "NOPE")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert "NOPE" not in response.text


async def test_the_prices_need_a_session_against_the_real_app(db_config: DbConfig) -> None:
    async with running_app(db_config.settings()) as (_, client):
        response = await client.get("/api/v1/stocks/DEMOA/prices")
    assert response.status_code == 401
