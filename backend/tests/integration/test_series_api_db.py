"""GET /api/v1/stocks/{symbol}/series over HTTP, against the real database.

One source, like with like: only screener.in's consolidated, rupee, full-year figures. All the
numbers here are synthetic.
"""

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


async def screener_fact(
    engine: AsyncEngine,
    *,
    symbol: str = "TCS",
    metric: str = "net_profit",
    year: int,
    value: Decimal,
    basis: str = "consolidated",
    unit: str = "INR_CRORE",
    currency: str | None = "INR",
    period: str | None = None,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO facts (stock_id, source, source_url, source_section, source_row, "
                "source_column, metric, period, period_end, basis, currency, unit, value) "
                "SELECT id, 'screener', :url, 'profit-loss', 'Net Profit', :column, :metric, "
                ":period, make_date(:year, 3, 31), :basis, :currency, :unit, :value "
                "FROM stocks WHERE symbol = :symbol"
            ),
            {
                "url": SCREENER,
                "column": f"Mar {year}",
                "metric": metric,
                "period": period or f"FY{year}",
                "year": year,
                "basis": basis,
                "currency": currency,
                "unit": unit,
                "value": value,
                "symbol": symbol,
            },
        )


async def filing_fact(engine: AsyncEngine, *, year: int, value: Decimal) -> None:
    async with engine.begin() as connection:
        document = (
            await connection.execute(
                text(
                    "INSERT INTO documents (stock_id, title, sha256, size_bytes, blob_key, "
                    "source, source_url, status, kind, period) SELECT id, 'TCS annual report', "
                    "repeat('a', 64), 10, 'documents/' || repeat('a', 64) || '.pdf', 'bse', :url, "
                    "'completed', 'annual_report', 'Annual Report' FROM stocks "
                    "WHERE symbol = 'TCS' RETURNING id"
                ),
                {"url": BSE},
            )
        ).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO facts (stock_id, source, document_id, page_number, quote, metric, "
                "period, period_end, basis, currency, unit, value) SELECT stock_id, 'filing', "
                "id, 44, 'DemoCo net profit quote', 'net_profit', :period, make_date(:year, 3, "
                "31), 'consolidated', 'INR', 'INR_CRORE', :value FROM documents WHERE id = :d"
            ),
            {"period": f"FY{year}", "year": year, "value": value, "d": document},
        )


async def get_series(db_config: DbConfig, cookie: dict[str, str], path: str, **params: Any) -> Any:
    async with running_app(db_config.settings()) as (_, client):
        return await client.get(path, headers=cookie, params=params)


async def test_the_series_is_screener_consolidated_full_years_oldest_first(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await screener_fact(admin_engine, year=2025, value=Decimal("120"))
    await screener_fact(admin_engine, year=2023, value=Decimal("100.5"))
    await screener_fact(admin_engine, year=2024, value=Decimal("-3"))
    # Not like with like, so all left out:
    await filing_fact(admin_engine, year=2022, value=Decimal("999"))
    await screener_fact(admin_engine, year=2022, value=Decimal("77"), basis="standalone")
    await screener_fact(admin_engine, year=2021, value=Decimal("66"), period="Q4FY2021")
    await screener_fact(admin_engine, year=2020, value=Decimal("55"), metric="total_equity")
    await screener_fact(admin_engine, symbol="RELIANCE", year=2026, value=Decimal("5"))
    _, cookie = await sign_in(make_user, session_factory)

    response = await get_series(db_config, cookie, "/api/v1/stocks/TCS/series", metric="net_profit")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "symbol": "TCS",
        "metric": "net_profit",
        "label": "Net profit",
        "unit": "INR_CRORE",
        "source": "screener.in, consolidated",
        "points": [
            {
                "period": f"FY{year}",
                "value": value,
                "citation": {
                    "source": "screener",
                    "label": f"screener.in · profit-loss · Net Profit · Mar {year}",
                    "url": SCREENER,
                    "quote": None,
                },
            }
            for year, value in [(2023, "100.5"), (2024, "-3"), (2025, "120")]
        ],
    }


async def test_at_most_the_ten_latest_years_come_back_oldest_first(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    for year in range(2010, 2023):  # 13 years
        await screener_fact(admin_engine, year=year, value=Decimal(year))
    _, cookie = await sign_in(make_user, session_factory)

    response = await get_series(db_config, cookie, "/api/v1/stocks/TCS/series", metric="net_profit")

    periods = [p["period"] for p in response.json()["points"]]
    assert periods == [f"FY{year}" for year in range(2013, 2023)]


async def test_another_metric_has_its_own_label(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await screener_fact(
        admin_engine, year=2025, value=Decimal("9"), metric="revenue_from_operations"
    )
    _, cookie = await sign_in(make_user, session_factory)

    response = await get_series(
        db_config, cookie, "/api/v1/stocks/TCS/series", metric="revenue_from_operations"
    )

    body = response.json()
    assert (body["metric"], body["label"]) == ("revenue_from_operations", "Revenue from operations")
    assert [p["value"] for p in body["points"]] == ["9"]


async def test_a_stock_with_no_figures_has_an_empty_series(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, cookie = await sign_in(make_user, session_factory)

    response = await get_series(
        db_config, cookie, "/api/v1/stocks/HDFCBANK/series", metric="net_interest_income"
    )

    assert response.status_code == 200
    assert response.json()["points"] == []


async def test_an_unknown_stock_is_404_without_repeating_the_symbol(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, cookie = await sign_in(make_user, session_factory)

    response = await get_series(
        db_config, cookie, "/api/v1/stocks/NOPE/series", metric="net_profit"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert "NOPE" not in response.text
