"""Re-checking stored facts after a validator rule is tightened, without paying the LLM again (P11).

Found in the first real run: the label rule for revenue accepted the loose word "revenue", so a
headline figure of another definition was stored as revenue from operations. The rule is now the
statutory line item; this re-check applies it to the quotes already stored and removes what no
longer passes. screener.in facts have no quote and are never touched.
"""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.fact_validation import label_in_quote
from app.recheck_facts import recheck_labels

pytestmark = pytest.mark.usefixtures("migrated_db", "clean_document_tables")


async def seed(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        document = (
            await connection.execute(
                text(
                    "INSERT INTO documents (stock_id, title, sha256, size_bytes, blob_key, source, "
                    "source_url, status) SELECT id, 'DemoCo AR', repeat('a', 64), 10, "
                    "'documents/' || repeat('a', 64) || '.pdf', 'bse', "
                    "'https://www.bseindia.com/xml-data/corpfiling/AttachHis/1.pdf', 'completed' "
                    "FROM stocks WHERE symbol = 'TCS' RETURNING id"
                )
            )
        ).scalar_one()
        for period, quote in (
            ("FY2026", "Consolidated revenue grew by 9.8% to ₹ 1,200 crore"),  # the loose word
            ("FY2025", "Revenue from Operations 26 1,000 950"),  # the statutory line
        ):
            await connection.execute(
                text(
                    "INSERT INTO facts (stock_id, source, document_id, page_number, quote, metric, "
                    "period, period_end, basis, currency, unit, value) SELECT stock_id, 'filing', "
                    "id, 4, :quote, 'revenue_from_operations', :period, DATE '2026-03-31', "
                    "'consolidated', 'INR', 'INR_CRORE', :value FROM documents WHERE id = :id"
                ),
                {"quote": quote, "period": period, "value": Decimal("1000"), "id": document},
            )
        await connection.execute(
            text(
                "INSERT INTO facts (stock_id, source, source_url, source_section, source_row, "
                "source_column, metric, period, period_end, basis, currency, unit, value) "
                "SELECT id, 'screener', 'https://www.screener.in/company/TCS/consolidated/', "
                "'profit-loss', 'Sales', 'Mar 2026', 'revenue_from_operations', 'FY2026', "
                "DATE '2026-03-31', 'consolidated', 'INR', 'INR_CRORE', 1100 FROM stocks "
                "WHERE symbol = 'TCS'"
            )
        )


async def test_facts_failing_the_current_label_rule_are_removed(
    session_factory: async_sessionmaker[AsyncSession], admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)

    async with session_factory() as db:
        removed = await recheck_labels(db)
        await db.commit()

    assert removed == 1
    async with admin_engine.connect() as connection:
        left = [
            tuple(row)
            for row in await connection.execute(
                text("SELECT source, period FROM facts ORDER BY source, period")
            )
        ]
    assert left == [("filing", "FY2025"), ("screener", "FY2026")]


async def test_running_it_again_removes_nothing(
    session_factory: async_sessionmaker[AsyncSession], admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    for expected in (1, 0):
        async with session_factory() as db:
            assert await recheck_labels(db) == expected
            await db.commit()


def test_the_label_check_is_the_validators_own() -> None:
    assert label_in_quote("revenue_from_operations", "Revenue from Operations 1,000")
    assert not label_in_quote("revenue_from_operations", "Consolidated revenue ₹ 1,000 crore")
    assert not label_in_quote("no_such_metric", "anything")


async def test_facts_failing_a_definition_guard_are_removed(
    session_factory: async_sessionmaker[AsyncSession], admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO facts (stock_id, source, document_id, page_number, quote, metric, "
                "period, period_end, basis, currency, unit, value) SELECT stock_id, 'filing', id, "
                "5, 'Net profit (excluding exceptional items) ₹ 900 crore', 'net_profit', "
                "'FY2026', DATE '2026-03-31', 'consolidated', 'INR', 'INR_CRORE', 900 "
                "FROM documents LIMIT 1"
            )
        )

    async with session_factory() as db:
        assert await recheck_labels(db) == 2  # the loose revenue and the adjusted profit
        await db.commit()
