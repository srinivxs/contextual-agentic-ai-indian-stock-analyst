"""GET /api/v1/stocks/{symbol}/insights over HTTP, against the real database (P11c).

The contract the stock page is built on: key facts with citations (a filing's page and quote, or
screener.in's section, row and column), the four derived values, rolling sentiment and recent
events. Amounts travel as strings, so no figure is rounded by a float on the way.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.helpers import running_app
from tests.integration.auth_helpers import open_session
from tests.integration.conftest import DbConfig, MakeUser

pytestmark = pytest.mark.usefixtures("clean_document_tables")

Factory = async_sessionmaker[AsyncSession]
BSE = "https://www.bseindia.com/xml-data/corpfiling/AttachHis/00000001-demo.pdf"
SCREENER = "https://www.screener.in/company/TCS/consolidated/"


async def sign_in(make_user: MakeUser, session_factory: Factory) -> tuple[UUID, dict[str, str]]:
    user_id = await make_user()
    token = await open_session(session_factory, user_id)
    return user_id, {"Cookie": f"session={token}"}


async def seed(engine: AsyncEngine, *, document_status: str = "completed") -> None:
    async with engine.begin() as connection:
        document = (
            await connection.execute(
                text(
                    "INSERT INTO documents (stock_id, title, sha256, size_bytes, blob_key, source, "
                    "source_url, status, kind, period) SELECT id, 'TCS annual report, "
                    "Annual Report 2026', repeat('a', 64), 10, 'documents/' || repeat('a', 64) "
                    "|| '.pdf', 'bse', :url, :status, 'annual_report', 'Annual Report 2026' "
                    "FROM stocks WHERE symbol = 'TCS' RETURNING id"
                ),
                {"url": BSE, "status": document_status},
            )
        ).scalar_one()
        facts: list[dict[str, Any]] = [
            {"metric": "total_borrowings", "period": "FY2026", "value": Decimal("250")},
            {"metric": "total_equity", "period": "FY2026", "value": Decimal("1000")},
            {"metric": "net_profit", "period": "FY2026", "value": Decimal("110")},
        ]
        for values in facts:
            await connection.execute(
                text(
                    "INSERT INTO facts (stock_id, source, document_id, page_number, quote, "
                    "metric, period, period_end, basis, currency, unit, value) "
                    "SELECT stock_id, 'filing', id, 44, :quote, :metric, :period, "
                    "DATE '2026-03-31', 'consolidated', 'INR', 'INR_CRORE', :value "
                    "FROM documents WHERE id = :document"
                ),
                {**values, "document": document, "quote": f"DemoCo {values['metric']} quote"},
            )
        await connection.execute(
            text(
                "INSERT INTO facts (stock_id, source, source_url, source_section, source_row, "
                "source_column, metric, period, period_end, basis, currency, unit, value) "
                "SELECT id, 'screener', :url, 'profit-loss', 'Net Profit', 'Mar 2025', "
                "'net_profit', 'FY2025', DATE '2025-03-31', 'consolidated', 'INR', 'INR_CRORE', "
                "100 FROM stocks WHERE symbol = 'TCS'"
            ),
            {"url": SCREENER},
        )
        await connection.execute(
            text(
                "INSERT INTO events (stock_id, document_id, page_number, event_type, sentiment, "
                "impact, event_date, date_source, summary, quote) SELECT stock_id, id, 2, "
                "'dividend', 'positive', 'low', :day, 'document', 'DemoCo declared a dividend.', "
                "'The Board declared a dividend.' FROM documents WHERE id = :document"
            ),
            {"document": document, "day": date.today()},
        )


async def test_the_insights_carry_every_figure_with_its_citation(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    _, cookie = await sign_in(make_user, session_factory)

    async with running_app(db_config.settings()) as (_, client):
        response = await client.get("/api/v1/stocks/TCS/insights", headers=cookie)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["symbol"], body["is_financial"]) == ("TCS", False)
    assert body["name"]

    by_key = {(f["metric"], f["period"]): f for f in body["key_facts"]}
    assert by_key[("net_profit", "FY2026")] == {
        "metric": "net_profit",
        "label": "Net profit",
        "period": "FY2026",
        "basis": "consolidated",
        "currency": "INR",
        "unit": "INR_CRORE",
        "value": "110.0000",
        "status": "single",
        "corroborated_by": 0,
        "citation": {
            "source": "filing",
            "label": "Annual report · Annual Report 2026 · p.44",
            "url": f"{BSE}#page=44",
            "quote": "DemoCo net_profit quote",
        },
        "disputed_by": [],
    }
    assert by_key[("net_profit", "FY2025")]["citation"] == {
        "source": "screener",
        "label": "screener.in · profit-loss · Net Profit · Mar 2025",
        "url": SCREENER,
        "quote": None,
    }

    derived = {d["name"]: d for d in body["derived"]}
    assert [d["name"] for d in body["derived"]] == [
        "debt_to_equity",
        "revenue_growth",
        "profit_growth",
        "latest_dividend",
    ]
    assert (derived["debt_to_equity"]["status"], derived["debt_to_equity"]["value"]) == (
        "ok",
        "0.25",
    )
    assert len(derived["debt_to_equity"]["citations"]) == 2
    # FY2026 comes from an annual report, FY2025 only from screener.in: growth compares like with
    # like (one kind of source), so a definition change can never pass for growth.
    assert (derived["profit_growth"]["status"], derived["profit_growth"]["value"]) == (
        "not_assessable",
        None,
    )
    assert derived["revenue_growth"]["value"] is None
    assert all(d["reason"] for d in body["derived"])

    assert body["sentiment"] == {
        "status": "insufficient_data",
        "score": None,
        "label": None,
        "events_counted": 1,
    }
    assert body["events"] == [
        {
            "event_type": "dividend",
            "sentiment": "positive",
            "impact": "low",
            "event_date": date.today().isoformat(),
            "summary": "DemoCo declared a dividend.",
            "citation": {
                "source": "filing",
                "label": "Annual report · Annual Report 2026 · p.2",
                "url": f"{BSE}#page=2",
                "quote": "The Board declared a dividend.",
            },
        }
    ]


async def test_a_document_being_read_again_does_not_show_its_facts(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine, document_status="processing")
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        body = (await client.get("/api/v1/stocks/TCS/insights", headers=cookie)).json()
    assert [(f["metric"], f["period"]) for f in body["key_facts"]] == [("net_profit", "FY2025")]


async def test_a_stock_with_nothing_yet_has_empty_insights(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        body = (await client.get("/api/v1/stocks/HDFCBANK/insights", headers=cookie)).json()
    assert body["key_facts"] == []
    assert body["events"] == []
    assert [d["status"] for d in body["derived"]] == [
        "not_applicable",  # a bank
        "not_assessable",
        "not_assessable",
        "insufficient_data",
    ]


async def test_an_unknown_stock_is_404(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        response = await client.get("/api/v1/stocks/INFY/insights", headers=cookie)
    assert response.status_code == 404
    assert "INFY" not in response.text
