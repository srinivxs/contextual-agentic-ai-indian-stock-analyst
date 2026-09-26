"""screener.in's fundamentals, stored as cited facts by the daily discovery (P11, ADR 020).

The worker already reads each stock's screener.in company page once a day for filing links
(ADR 018). The owner decided its fundamentals table is used too: the same page, parsed by code
(app/screener_numbers.py, no LLM), each figure stored with its citation (the page URL and the
section, row and column). A new day's read updates a figure in place; nothing is duplicated.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.blobs import FilesystemBlobStore
from app.filings import SCREENER_URL, parse_documents
from app.worker import WorkerContext, run_once
from tests.filings_html import TODAY
from tests.screener_html import DEMOBANK_PAGE, DEMOCO_PAGE

pytestmark = pytest.mark.usefixtures("migrated_db", "clean_document_tables")

Factory = async_sessionmaker[AsyncSession]


class ScreenerOnly:
    """The internet as discovery sees it: one screener page per symbol."""

    def __init__(self) -> None:
        self.pages: dict[str, str] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        for symbol, page in self.pages.items():
            if str(request.url) == SCREENER_URL.format(symbol=symbol):
                return httpx.Response(200, text=page)
        return httpx.Response(404)


@pytest.fixture
def internet() -> ScreenerOnly:
    return ScreenerOnly()


@pytest.fixture
async def context(
    session_factory: Factory, tmp_path: Path, internet: ScreenerOnly
) -> AsyncIterator[WorkerContext]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(internet)) as http:
        yield WorkerContext(
            session_factory=session_factory,
            blob_store=FilesystemBlobStore(tmp_path / "blobs"),
            lease_seconds=300,
            http=http,
            fetch_pause_seconds=0,
            today=lambda: TODAY,
        )


async def discover(engine: AsyncEngine, context: WorkerContext, symbol: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('discover_filings', "
                "jsonb_build_object('symbol', CAST(:symbol AS text)), "
                "'discover_filings:' || :symbol)"
            ),
            {"symbol": symbol},
        )
    assert await run_once(context)


async def rows(engine: AsyncEngine, sql: str) -> list[tuple[Any, ...]]:
    async with engine.connect() as connection:
        return [tuple(row) for row in await connection.execute(text(sql))]


async def test_discovery_stores_the_fundamentals_as_cited_facts(
    context: WorkerContext, internet: ScreenerOnly, admin_engine: AsyncEngine
) -> None:
    internet.pages["TCS"] = DEMOCO_PAGE

    await discover(admin_engine, context, "TCS")

    assert await rows(admin_engine, "SELECT status FROM jobs") == [("completed",)]
    [(count, metrics)] = await rows(
        admin_engine,
        "SELECT count(*), array_agg(DISTINCT metric ORDER BY metric) FROM facts "
        "WHERE source = 'screener'",
    )
    assert count == 25
    assert metrics == [
        "eps_basic",
        "net_profit",
        "revenue_from_operations",
        "total_borrowings",
        "total_equity",
    ]
    assert await rows(
        admin_engine,
        "SELECT s.symbol, f.source_url, f.source_section, f.source_row, f.source_column, "
        "f.period, f.basis, f.currency, f.unit, f.value::text FROM facts f "
        "JOIN stocks s ON s.id = f.stock_id "
        "WHERE f.metric = 'revenue_from_operations' AND f.period = 'Q2FY2026'",
    ) == [
        (
            "TCS",
            SCREENER_URL.format(symbol="TCS"),
            "quarters",
            "Sales",
            "Sep 2025",
            "Q2FY2026",
            "consolidated",
            "INR",
            "INR_CRORE",
            "1234.0000",
        )
    ]


async def test_a_bank_gets_no_revenue_or_borrowings(
    context: WorkerContext, internet: ScreenerOnly, admin_engine: AsyncEngine
) -> None:
    """A bank's "revenue" and "borrowing" mean something else; debt to equity does not apply."""
    internet.pages["HDFCBANK"] = DEMOBANK_PAGE

    await discover(admin_engine, context, "HDFCBANK")

    assert await rows(admin_engine, "SELECT DISTINCT metric FROM facts ORDER BY metric") == [
        ("eps_basic",),
        ("net_profit",),
        ("return_on_equity",),
        ("total_equity",),
    ]


async def test_reading_again_updates_in_place_and_duplicates_nothing(
    context: WorkerContext, internet: ScreenerOnly, admin_engine: AsyncEngine
) -> None:
    internet.pages["TCS"] = DEMOCO_PAGE
    await discover(admin_engine, context, "TCS")
    async with admin_engine.begin() as connection:  # the next day
        await connection.execute(text("UPDATE jobs SET created_at = now() - interval '2 days'"))
        await connection.execute(
            text("UPDATE facts SET updated_at = now() - interval '1 day', value = value - 1")
        )

    await discover(admin_engine, context, "TCS")

    assert await rows(admin_engine, "SELECT count(*) FROM facts") == [(25,)]
    assert await rows(
        admin_engine,
        "SELECT value::text, updated_at > now() - interval '1 hour' FROM facts "
        "WHERE metric = 'revenue_from_operations' AND period = 'Q2FY2026'",
    ) == [("1234.0000", True)]


async def test_a_page_without_fundamentals_leaves_the_stored_ones_alone(
    context: WorkerContext, internet: ScreenerOnly, admin_engine: AsyncEngine
) -> None:
    """A layout change should stop updates, not wipe the history."""
    internet.pages["TCS"] = DEMOCO_PAGE
    await discover(admin_engine, context, "TCS")
    async with admin_engine.begin() as connection:
        await connection.execute(text("UPDATE jobs SET created_at = now() - interval '2 days'"))
    internet.pages["TCS"] = "<html><body>A redesigned page</body></html>"

    await discover(admin_engine, context, "TCS")

    assert await rows(admin_engine, "SELECT count(*) FROM facts") == [(25,)]


def test_broken_markup_gives_no_links_rather_than_a_crash() -> None:
    """html.parser raises AssertionError on some malformed declarations ("<![foo[")."""
    assert parse_documents("<html><![foo[ broken") == []
