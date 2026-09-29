"""The Fundamentals card (the owner, 2026-09-30): screener.in's top ratios stored per stock
from the page discovery reads, and served with our own debt to equity. Synthetic numbers only."""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.screener_facts import store_top_ratios
from tests.helpers import running_app
from tests.integration.auth_helpers import open_session
from tests.integration.conftest import DbConfig, MakeUser
from tests.screener_html import DEMOCO_PAGE

pytestmark = pytest.mark.usefixtures("clean_document_tables")

Factory = async_sessionmaker[AsyncSession]
URL = "https://www.screener.in/company/TCS/consolidated/"


async def stock_id(engine: AsyncEngine, symbol: str) -> int:
    async with engine.connect() as connection:
        found = await connection.execute(
            text("SELECT id FROM stocks WHERE symbol = :symbol"), {"symbol": symbol}
        )
        return int(found.scalar_one())


async def stored(engine: AsyncEngine) -> list[tuple[Any, ...]]:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT market_cap_crore, stock_pe, book_value, source_url FROM screener_ratios")
        )
        return [tuple(row) for row in result]


async def test_a_read_stores_the_top_ratios_and_the_next_read_replaces_them(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    tcs = await stock_id(admin_engine, "TCS")
    async with session_factory() as db:
        assert await store_top_ratios(db, stock_id=tcs, page=DEMOCO_PAGE, url=URL) is True
        await db.commit()
    first = await stored(admin_engine)
    changed = DEMOCO_PAGE.replace("22.5", "30.5")
    async with session_factory() as db:
        await store_top_ratios(db, stock_id=tcs, page=changed, url=URL)
        await db.commit()

    assert [(str(a), str(b), str(c), d) for a, b, c, d in first] == [("123456", "22.5", "610", URL)]
    assert [str(row[1]) for row in await stored(admin_engine)] == ["30.5"]  # one row, replaced


async def test_a_page_with_no_top_ratios_changes_nothing(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    tcs = await stock_id(admin_engine, "TCS")
    async with session_factory() as db:
        await store_top_ratios(db, stock_id=tcs, page=DEMOCO_PAGE, url=URL)
        assert await store_top_ratios(db, stock_id=tcs, page="<html></html>", url=URL) is False
        await db.commit()
    assert len(await stored(admin_engine)) == 1


async def test_the_card_serves_the_stored_ratios_with_our_debt_to_equity(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    tcs = await stock_id(admin_engine, "TCS")
    async with session_factory() as db:
        await store_top_ratios(db, stock_id=tcs, page=DEMOCO_PAGE, url=URL)
        await db.commit()
    token = await open_session(session_factory, await make_user())
    async with running_app(db_config.settings()) as (_, client):
        response = await client.get(
            "/api/v1/stocks/TCS/fundamentals", headers={"Cookie": f"session={token}"}
        )
        missing = await client.get(
            "/api/v1/stocks/NOSUCH/fundamentals", headers={"Cookie": f"session={token}"}
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["symbol"], body["source_url"]) == ("TCS", URL)
    assert body["as_of"] is not None
    items = {item["label"]: item for item in body["items"]}
    assert list(items) == [
        "Mkt Cap",
        "ROE",
        "P/E Ratio (TTM)",
        "EPS (TTM)",
        "P/B Ratio",
        "Div Yield",
        "Industry P/E",
        "Book Value",
        "Debt to Equity",
        "Face Value",
    ]
    assert (items["Mkt Cap"]["value"], items["Mkt Cap"]["unit"]) == ("123456", "INR_CRORE")
    assert items["EPS (TTM)"]["value"] == "137.78"  # 3,100 / 22.5
    assert items["Industry P/E"]["status"] == "not_available"
    assert items["Debt to Equity"]["status"] == "not_available"  # no stored figures here
    assert missing.status_code == 404


async def test_before_the_first_read_the_card_says_nothing_is_stored(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    token = await open_session(session_factory, await make_user())
    async with running_app(db_config.settings()) as (_, client):
        body = (
            await client.get(
                "/api/v1/stocks/HDFCBANK/fundamentals", headers={"Cookie": f"session={token}"}
            )
        ).json()
    assert (body["as_of"], body["source_url"]) == (None, None)
    by_label = {item["label"]: item["status"] for item in body["items"]}
    assert by_label["Mkt Cap"] == "not_available"
    assert by_label["Debt to Equity"] == "not_applicable"  # a bank
