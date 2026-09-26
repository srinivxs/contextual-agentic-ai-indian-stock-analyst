"""Revision 0007: facts, events, extraction_calls and the extract_document job kind (P11, ADR 020).

A fact comes from one of two sources and carries that source's citation, enforced by the database:

- a filing (read by the LLM, checked by the validator): document, page and a verbatim quote;
- screener.in's fundamentals table (parsed by code): the page URL, section, row and column.

Money keeps the currency it was reported in (the owner's decision): the unit and the currency must
agree, so a US$ amount can never be mistaken for ₹ crore.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import Migrator

pytestmark = pytest.mark.usefixtures("migrated_db", "clean_document_tables")

SCREENER = "https://www.screener.in/company/TCS/consolidated/"


async def add_document(engine: AsyncEngine) -> int:
    async with engine.begin() as connection:
        found: int = (
            await connection.execute(
                text(
                    "INSERT INTO documents (stock_id, title, sha256, size_bytes, blob_key, source, "
                    "source_url, status) SELECT id, 'DemoCo filing', repeat('a', 64), 10, "
                    "'documents/' || repeat('a', 64) || '.pdf', 'bse', "
                    "'https://www.bseindia.com/xml-data/corpfiling/AttachHis/1.pdf', 'completed' "
                    "FROM stocks WHERE symbol = 'TCS' RETURNING id"
                )
            )
        ).scalar_one()
        return found


def filing_fact(document: int, /, **overrides: Any) -> dict[str, Any]:
    return {
        "source": "filing",
        "document_id": document,
        "page_number": 4,
        "quote": "Net profit for FY26 was ₹1,234 crore",
        "source_url": None,
        "source_section": None,
        "source_row": None,
        "source_column": None,
        "metric": "net_profit",
        "period": "FY2026",
        "period_end": date(2026, 3, 31),
        "basis": "consolidated",
        "currency": "INR",
        "unit": "INR_CRORE",
        "value": Decimal("1234"),
        **overrides,
    }


def screener_fact(**overrides: Any) -> dict[str, Any]:
    screener = {
        "source": "screener",
        "document_id": None,
        "page_number": None,
        "quote": None,
        "source_url": SCREENER,
        "source_section": "profit-loss",
        "source_row": "Net Profit",
        "source_column": "Mar 2026",
    }
    return {**filing_fact(0), **screener, **overrides}


async def insert_fact(engine: AsyncEngine, values: dict[str, Any]) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO facts (stock_id, source, document_id, page_number, quote, source_url, "
                "source_section, source_row, source_column, metric, period, period_end, basis, "
                "currency, unit, value) SELECT id, :source, :document_id, :page_number, :quote, "
                ":source_url, :source_section, :source_row, :source_column, :metric, :period, "
                ":period_end, :basis, :currency, :unit, :value "
                "FROM stocks WHERE symbol = 'TCS'"
            ),
            values,
        )


async def count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as connection:
        found: int = (
            await connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
        ).scalar_one()
        return found


# --- facts ----------------------------------------------------------------------------------------


async def test_a_filing_fact_and_a_screener_fact_are_both_stored(admin_engine: AsyncEngine) -> None:
    document = await add_document(admin_engine)
    await insert_fact(admin_engine, filing_fact(document))
    await insert_fact(admin_engine, screener_fact())
    assert await count(admin_engine, "facts") == 2


async def test_a_us_dollar_amount_is_stored_as_reported(admin_engine: AsyncEngine) -> None:
    document = await add_document(admin_engine)
    await insert_fact(
        admin_engine,
        filing_fact(
            document,
            metric="revenue_from_operations",
            currency="USD",
            unit="USD_MILLION",
            value=Decimal("1800"),
            quote="Revenue of US$1.8 billion",
        ),
    )
    assert await count(admin_engine, "facts") == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"quote": None},  # a filing fact without its quote
        {"page_number": None},
        {"document_id": None},
        {"source_url": SCREENER},  # a filing fact cannot also claim a screener citation
        {"metric": "market_mood"},
        {"period": "2026"},
        {"basis": "roughly"},
        {"currency": "EUR"},
        {"unit": "USD_MILLION"},  # unit and currency disagree
        {"currency": None},  # an amount must have a currency
        {"quote": "x" * 401},
    ],
    ids=[
        "no-quote",
        "no-page",
        "no-document",
        "mixed-citation",
        "unknown-metric",
        "bad-period",
        "bad-basis",
        "other-currency",
        "unit-currency-mismatch",
        "amount-without-currency",
        "quote-too-long",
    ],
)
async def test_a_filing_fact_without_a_proper_citation_or_unit_is_refused(
    admin_engine: AsyncEngine, overrides: dict[str, Any]
) -> None:
    document = await add_document(admin_engine)
    with pytest.raises(DBAPIError):
        await insert_fact(admin_engine, filing_fact(document, **overrides))
    assert await count(admin_engine, "facts") == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_row": None},
        {"source_url": None},
        {"quote": "a quote"},  # a screener fact has a row, not a quote
        {"source_url": "https://evil.example/company/TCS/"},
    ],
    ids=["no-row", "no-url", "quote-instead-of-row", "not-screener"],
)
async def test_a_screener_fact_must_cite_screener_by_section_row_and_column(
    admin_engine: AsyncEngine, overrides: dict[str, Any]
) -> None:
    with pytest.raises(DBAPIError):
        await insert_fact(admin_engine, screener_fact(**overrides))


async def test_a_percentage_has_no_currency(admin_engine: AsyncEngine) -> None:
    document = await add_document(admin_engine)
    await insert_fact(
        admin_engine,
        filing_fact(
            document,
            metric="return_on_equity",
            currency=None,
            unit="PERCENT",
            value=Decimal("12.5"),
        ),
    )
    with pytest.raises(DBAPIError):
        await insert_fact(
            admin_engine,
            filing_fact(
                document,
                metric="return_on_equity",
                currency="INR",
                unit="PERCENT",
                value=Decimal("1"),
            ),
        )


async def test_one_fact_per_filing_metric_period_basis_and_currency(
    admin_engine: AsyncEngine,
) -> None:
    document = await add_document(admin_engine)
    await insert_fact(admin_engine, filing_fact(document))
    with pytest.raises(IntegrityError):
        await insert_fact(admin_engine, filing_fact(document, page_number=9))


async def test_one_percentage_per_filing_metric_period_and_basis(admin_engine: AsyncEngine) -> None:
    """A percentage has no currency (NULL), and a plain unique index treats every NULL as
    different, so two identical percentages would both go in. The index says NULLS NOT DISTINCT."""
    document = await add_document(admin_engine)
    roe = {"metric": "return_on_equity", "currency": None, "unit": "PERCENT"}
    await insert_fact(admin_engine, filing_fact(document, **roe, value=Decimal("12.5")))
    with pytest.raises(IntegrityError):
        await insert_fact(admin_engine, filing_fact(document, **roe, value=Decimal("12.5")))


async def test_one_screener_fact_per_stock_metric_period_and_basis(
    admin_engine: AsyncEngine,
) -> None:
    await insert_fact(admin_engine, screener_fact())
    with pytest.raises(IntegrityError):
        await insert_fact(admin_engine, screener_fact(value=Decimal("999")))


async def test_deleting_a_document_removes_its_facts_events_and_calls(
    admin_engine: AsyncEngine,
) -> None:
    document = await add_document(admin_engine)
    await insert_fact(admin_engine, filing_fact(document))
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO events (stock_id, document_id, page_number, event_type, sentiment, "
                "impact, event_date, date_source, summary, quote) SELECT stock_id, id, 1, "
                "'dividend', 'positive', 'low', DATE '2026-01-15', 'document', 'A dividend', "
                "'The board declared a dividend' FROM documents WHERE id = :id"
            ),
            {"id": document},
        )
        await connection.execute(
            text(
                "INSERT INTO extraction_calls (document_id, pass, first_page, last_page, model, "
                "version, input_tokens, output_tokens, accepted) "
                "VALUES (:id, 'facts', 1, 3, 'fake-llm', 'v1', 1000, 200, 1)"
            ),
            {"id": document},
        )
        await connection.execute(text("DELETE FROM documents WHERE id = :id"), {"id": document})

    assert [await count(admin_engine, t) for t in ("facts", "events", "extraction_calls")] == [
        0,
        0,
        0,
    ]


# --- events and calls --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("event_type", "'rumour'"),
        ("sentiment", "'ecstatic'"),
        ("impact", "'huge'"),
        ("date_source", "'guess'"),
        ("quote", "repeat('x', 401)"),
        ("summary", "repeat('x', 201)"),
    ],
)
async def test_an_event_outside_the_fixed_lists_is_refused(
    admin_engine: AsyncEngine, column: str, value: str
) -> None:
    document = await add_document(admin_engine)
    values = {
        "event_type": "'dividend'",
        "sentiment": "'positive'",
        "impact": "'low'",
        "date_source": "'document'",
        "summary": "'A dividend'",
        "quote": "'The board declared a dividend'",
        column: value,
    }
    with pytest.raises(DBAPIError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    # The values are this test's own constants, never outside input.
                    "INSERT INTO events (stock_id, document_id, page_number, event_type, "  # noqa: S608
                    "sentiment, impact, event_date, date_source, summary, quote) "
                    f"SELECT stock_id, id, 1, {values['event_type']}, {values['sentiment']}, "
                    f"{values['impact']}, DATE '2026-01-15', {values['date_source']}, "
                    f"{values['summary']}, {values['quote']} FROM documents WHERE id = :id"
                ),
                {"id": document},
            )


async def test_the_queue_accepts_extract_document_jobs(admin_engine: AsyncEngine) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, payload, dedupe_key) "
                "VALUES ('extract_document', '{\"document_id\": 1}', 'extract_document:1:v1')"
            )
        )


async def test_the_runtime_role_can_write_all_three_tables(app_engine: AsyncEngine) -> None:
    document = await add_document(app_engine)
    await insert_fact(app_engine, filing_fact(document))
    assert await count(app_engine, "facts") == 1


async def test_downgrading_removes_the_tables_and_the_job_kind(
    migrator: Migrator, admin_engine: AsyncEngine
) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, payload, dedupe_key) "
                "VALUES ('extract_document', '{\"document_id\": 1}', 'extract_document:1:v1')"
            )
        )
    await migrator.downgrade("0006")
    try:
        async with admin_engine.connect() as connection:
            tables = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                        "AND tablename IN ('facts', 'events', 'extraction_calls')"
                    )
                )
            ).scalar_one()
            jobs = (await connection.execute(text("SELECT count(*) FROM jobs"))).scalar_one()
        assert (tables, jobs) == (0, 0)
    finally:
        await migrator.upgrade("head")
