"""Revision 0005: documents.kind and documents.period, and the backfill of rows 0004 already held.

The Documents page groups filings by kind (transcripts, presentations, annual reports,
announcements) and orders them by period. 0004 had already been applied with real rows when this
was needed, so it is a new, forward-only migration that fills the columns in for existing rows from
their titles, which the worker has always written as "<SYMBOL> <kind words>, <period>".
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import Migrator

pytestmark = pytest.mark.usefixtures("clean_document_tables")

SHA = "ab" * 32


async def insert(engine: AsyncEngine, title: str, sha: str, source: str = "bse") -> None:
    url = f"https://www.bseindia.com/xml-data/corpfiling/AttachHis/{sha[:36]}.pdf"
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO documents "
                "(stock_id, title, sha256, size_bytes, blob_key, source, source_url) "
                "SELECT id, :title, :sha, 10, 'documents/' || :sha || '.pdf', :source, :url "
                "FROM stocks WHERE symbol = 'TCS'"
            ),
            {"title": title, "sha": sha, "source": source, "url": url if source == "bse" else None},
        )


async def kinds(engine: AsyncEngine) -> list[tuple[str, str | None, str | None]]:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT title, kind, period FROM documents ORDER BY id")
        )
        return [(row.title, row.kind, row.period) for row in result]


async def test_existing_filings_get_their_kind_and_period_from_their_titles(
    migrator: Migrator, admin_engine: AsyncEngine
) -> None:
    await migrator.upgrade("head")
    async with admin_engine.begin() as connection:
        await connection.execute(text("TRUNCATE documents, jobs RESTART IDENTITY CASCADE"))
    await migrator.downgrade("0004")
    titles = [
        "TCS earnings call transcript, Jul 2026",
        "TCS investor presentation, Aug 2024",
        "TCS annual report, Annual Report 2025",
        "TCS announcement, Press Release - Results, with a comma",
        "Uploaded by hand",
    ]
    for number, title in enumerate(titles):
        await insert(
            admin_engine, title, f"{number:02d}" * 32, source="upload" if number == 4 else "bse"
        )

    await migrator.upgrade("head")

    assert await kinds(admin_engine) == [
        ("TCS earnings call transcript, Jul 2026", "transcript", "Jul 2026"),
        ("TCS investor presentation, Aug 2024", "presentation", "Aug 2024"),
        ("TCS annual report, Annual Report 2025", "annual_report", "Annual Report 2025"),
        (
            "TCS announcement, Press Release - Results, with a comma",
            "announcement",
            "Press Release - Results, with a comma",
        ),
        ("Uploaded by hand", None, None),  # an upload has no kind or period of its own
    ]


async def test_a_kind_outside_the_four_is_refused(
    migrated_db: None, admin_engine: AsyncEngine
) -> None:
    await insert(admin_engine, "TCS annual report, Annual Report 2025", SHA)
    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as connection:
            await connection.execute(text("UPDATE documents SET kind = 'rumour'"))


async def test_downgrading_removes_only_the_two_columns(
    migrator: Migrator, admin_engine: AsyncEngine
) -> None:
    await migrator.upgrade("head")
    await migrator.downgrade("0004")
    async with admin_engine.connect() as connection:
        columns = (
            await connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'documents' AND column_name IN ('kind', 'period')"
                )
            )
        ).all()
    await migrator.upgrade("head")  # leave the test database at head
    assert columns == []
