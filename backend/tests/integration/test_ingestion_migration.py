"""Revision 0004: documents, document_pages, chunks and jobs, checked in the real database.

Every rule that keeps ingestion idempotent lives here as a constraint, so these tests insert rows
directly and expect the DATABASE to refuse the bad ones. The application code merely relies on it.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.usefixtures("migrated_db", "clean_document_tables")

SHA = "ab" * 32
OTHER_SHA = "cd" * 32


async def insert_document(engine: AsyncEngine, sha: str = SHA, **overrides: object) -> int:
    values: dict[str, object] = {
        "symbol": "TCS",
        "title": "Q2 results",
        "sha256": sha,
        "size_bytes": 1234,
        "blob_key": f"documents/{sha}.pdf",
        "source": "upload",
        "source_url": None,
        **overrides,
    }
    async with engine.begin() as connection:
        result = await connection.execute(
            text(
                "INSERT INTO documents "
                "(stock_id, title, sha256, size_bytes, blob_key, source, source_url) "
                "SELECT id, :title, :sha256, :size_bytes, :blob_key, :source, :source_url "
                "FROM stocks WHERE symbol = :symbol RETURNING id"
            ),
            values,
        )
        document_id: int = result.scalar_one()
        return document_id


async def insert_job(engine: AsyncEngine, key: str, status: str = "pending") -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, payload, dedupe_key, status) "
                "VALUES ('ingest_document', '{}'::jsonb, :key, :status)"
            ),
            {"key": key, "status": status},
        )


async def columns(engine: AsyncEngine, table: str) -> list[str]:
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :table ORDER BY ordinal_position"
            ),
            {"table": table},
        )
        return list(result.scalars())


async def test_the_tables_have_the_agreed_columns(admin_engine: AsyncEngine) -> None:
    assert await columns(admin_engine, "documents") == [
        "id", "stock_id", "uploaded_by", "title", "sha256", "size_bytes", "blob_key",
        "source", "source_url", "status", "failure_reason", "page_count", "created_at",
        "updated_at",
    ]  # fmt: skip
    assert await columns(admin_engine, "document_pages") == ["document_id", "page_number", "text"]
    assert await columns(admin_engine, "chunks") == [
        "id", "document_id", "ordinal", "page_number", "text", "content_hash",
    ]  # fmt: skip
    assert await columns(admin_engine, "jobs") == [
        "id", "kind", "payload", "dedupe_key", "status", "attempts", "max_attempts",
        "run_after", "locked_until", "last_error", "created_at", "updated_at",
    ]  # fmt: skip


async def test_a_new_document_starts_pending(admin_engine: AsyncEngine) -> None:
    document_id = await insert_document(admin_engine)
    async with admin_engine.connect() as connection:
        status = (
            await connection.execute(
                text("SELECT status FROM documents WHERE id = :id"), {"id": document_id}
            )
        ).scalar_one()
    assert status == "pending"


async def test_the_same_file_can_never_be_stored_twice(admin_engine: AsyncEngine) -> None:
    """One SHA-256, one document: the rule that makes duplicate uploads converge."""
    await insert_document(admin_engine)
    with pytest.raises(IntegrityError):
        await insert_document(admin_engine, symbol="RELIANCE")


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"sha": "AB" * 32}, "upper-case hex"),
        ({"sha": "ab" * 31}, "too short"),
        ({"size_bytes": 0}, "empty file"),
        ({"title": ""}, "blank title"),
        ({"title": "x" * 201}, "title too long"),
    ],
)
async def test_malformed_documents_are_refused(
    admin_engine: AsyncEngine, overrides: dict[str, object], why: str
) -> None:
    sha = str(overrides.pop("sha", SHA))
    with pytest.raises(IntegrityError):
        await insert_document(admin_engine, sha=sha, **overrides)


async def test_the_blob_key_is_always_derived_from_the_hash(admin_engine: AsyncEngine) -> None:
    """blob_key has no unique constraint of its own: it follows from sha256, and a CHECK says so.

    A second unique constraint would be a second ON CONFLICT arbiter, and `ON CONFLICT (sha256)`
    does not cover it: concurrent duplicate uploads then fail instead of converging."""
    with pytest.raises(IntegrityError):
        await insert_document(admin_engine, blob_key=f"documents/{OTHER_SHA}.pdf")
    with pytest.raises(IntegrityError):
        await insert_document(admin_engine, blob_key="../../etc/passwd")
    async with admin_engine.connect() as connection:
        unique = (
            await connection.execute(
                text(
                    "SELECT array_agg(conname ORDER BY conname) FROM pg_constraint "
                    "WHERE conrelid = 'documents'::regclass AND contype = 'u'"
                )
            )
        ).scalar_one()
    assert unique == ["uq_documents_sha256"]


async def test_a_new_document_is_an_upload_with_no_source_url(admin_engine: AsyncEngine) -> None:
    document_id = await insert_document(admin_engine)
    async with admin_engine.connect() as connection:
        source = (
            await connection.execute(
                text("SELECT source, source_url FROM documents WHERE id = :id"), {"id": document_id}
            )
        ).one()
    assert tuple(source) == ("upload", None)


BSE_URL = "https://www.bseindia.com/stockinfo/AnnPdfOpen.aspx?Pname=x.pdf"


@pytest.mark.parametrize(
    ("source", "source_url"),
    [
        ("bse", None),  # a fetched filing must say where it came from
        ("bse", "https://www.example.com/report.pdf"),  # and only BSE is a source (ADR 018)
        ("upload", BSE_URL),  # an upload has no public source address
        ("scraped", BSE_URL),  # no other kinds of source
    ],
)
async def test_the_source_and_its_address_must_agree(
    admin_engine: AsyncEngine, source: str, source_url: str | None
) -> None:
    with pytest.raises(IntegrityError):
        await insert_document(admin_engine, source=source, source_url=source_url)


async def test_one_official_address_is_one_document(admin_engine: AsyncEngine) -> None:
    await insert_document(admin_engine, source="bse", source_url=BSE_URL)
    with pytest.raises(IntegrityError):
        await insert_document(admin_engine, sha=OTHER_SHA, source="bse", source_url=BSE_URL)


async def test_the_filing_job_kinds_exist(admin_engine: AsyncEngine) -> None:
    async with admin_engine.begin() as connection:
        for kind in ("discover_filings", "fetch_filing"):
            await connection.execute(
                text("INSERT INTO jobs (kind, dedupe_key) VALUES (:kind, :kind)"), {"kind": kind}
            )


async def test_a_document_status_outside_the_four_is_refused(admin_engine: AsyncEngine) -> None:
    document_id = await insert_document(admin_engine)
    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                text("UPDATE documents SET status = 'done' WHERE id = :id"), {"id": document_id}
            )


async def test_one_active_job_per_dedupe_key(admin_engine: AsyncEngine) -> None:
    """The partial unique index: a second PENDING job for one key is impossible."""
    await insert_job(admin_engine, "ingest_document:1")
    with pytest.raises(IntegrityError):
        await insert_job(admin_engine, "ingest_document:1")
    with pytest.raises(IntegrityError):
        await insert_job(admin_engine, "ingest_document:1", status="processing")


async def test_a_finished_job_does_not_block_a_new_one_for_the_same_key(
    admin_engine: AsyncEngine,
) -> None:
    """Re-ingesting later must be possible: the rule covers only pending and processing jobs."""
    await insert_job(admin_engine, "ingest_document:2", status="completed")
    await insert_job(admin_engine, "ingest_document:2", status="failed")
    await insert_job(admin_engine, "ingest_document:2")  # no error


@pytest.mark.parametrize(
    ("column", "value"), [("kind", "send_email"), ("status", "running"), ("attempts", -1)]
)
async def test_malformed_jobs_are_refused(
    admin_engine: AsyncEngine, column: str, value: object
) -> None:
    await insert_job(admin_engine, "ingest_document:3")
    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(f"UPDATE jobs SET {column} = :value WHERE dedupe_key = 'ingest_document:3'"),  # noqa: S608 - column names from the parametrize list above
                {"value": value},
            )


async def test_a_chunk_position_is_unique_within_its_document(admin_engine: AsyncEngine) -> None:
    document_id = await insert_document(admin_engine)
    insert = text(
        "INSERT INTO chunks (document_id, ordinal, page_number, text, content_hash) "
        "VALUES (:id, 0, 1, 'text', :hash)"
    )
    async with admin_engine.begin() as connection:
        await connection.execute(insert, {"id": document_id, "hash": SHA})
    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as connection:
            await connection.execute(insert, {"id": document_id, "hash": OTHER_SHA})


async def test_deleting_a_document_removes_its_pages_and_chunks(admin_engine: AsyncEngine) -> None:
    document_id = await insert_document(admin_engine)
    async with admin_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO document_pages VALUES (:id, 1, 'page one')"), {"id": document_id}
        )
        await connection.execute(
            text(
                "INSERT INTO chunks (document_id, ordinal, page_number, text, content_hash) "
                "VALUES (:id, 0, 1, 'page one', :hash)"
            ),
            {"id": document_id, "hash": SHA},
        )
        await connection.execute(text("DELETE FROM documents WHERE id = :id"), {"id": document_id})
        left = (
            await connection.execute(
                text("SELECT (SELECT count(*) FROM document_pages) + (SELECT count(*) FROM chunks)")
            )
        ).scalar_one()
    assert left == 0


async def test_the_runtime_role_can_use_every_new_table(
    app_engine: AsyncEngine, admin_engine: AsyncEngine
) -> None:
    """Default privileges (ADR 011) must cover tables a later migration creates."""
    document_id = await insert_document(app_engine)
    await insert_job(app_engine, "ingest_document:4")
    async with app_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO document_pages VALUES (:id, 1, 'page one')"), {"id": document_id}
        )
        await connection.execute(
            text(
                "INSERT INTO chunks (document_id, ordinal, page_number, text, content_hash) "
                "VALUES (:id, 0, 1, 'page one', :hash)"
            ),
            {"id": document_id, "hash": SHA},
        )
        await connection.execute(
            text("UPDATE jobs SET status = 'processing' WHERE dedupe_key = 'ingest_document:4'")
        )
