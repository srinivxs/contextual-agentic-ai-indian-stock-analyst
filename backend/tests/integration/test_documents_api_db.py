"""Recording documents and reading them over HTTP, against the real database.

The P9 acceptance rule is the heart of this file: the same file recorded twice, or eight times at
once, gives ONE document and ONE ingestion job. It holds because the database has a unique
constraint on the file's SHA-256 plus a partial unique index on the job's dedupe key. No
application-level lock is involved. Documents are recorded the way the worker records a fetched
filing (``record_document``); there is no upload endpoint (P9d).
"""

import asyncio
import hashlib
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.blobs import blob_key_for
from app.documents import DocumentView, PdfFile, record_document, stock_id
from tests.helpers import running_app
from tests.integration.auth_helpers import open_session
from tests.integration.conftest import DbConfig, MakeUser

pytestmark = pytest.mark.usefixtures("clean_document_tables")

Factory = async_sessionmaker[AsyncSession]
DATA = b"%PDF-1.7\n" + b"fictional DemoCo quarterly results " * 200


def pdf_of(data: bytes = DATA) -> PdfFile:
    return PdfFile(data=data, sha256=hashlib.sha256(data).hexdigest())


def bse_url(n: int) -> str:
    return f"https://www.bseindia.com/xml-data/corpfiling/AttachHis/{n:08d}-demo.pdf"


async def record(
    factory: Factory,
    *,
    symbol: str = "TCS",
    data: bytes = DATA,
    url: str = bse_url(1),
    period: str = "Jul 2026",
) -> tuple[DocumentView, bool]:
    """One transaction, exactly as a filing fetch records what it downloaded."""
    pdf = pdf_of(data)
    async with factory() as db:
        stock = await stock_id(db, symbol)
        assert stock is not None
        result = await record_document(
            db,
            stock=stock,
            title=f"{symbol} earnings call transcript, {period}",
            pdf=pdf,
            blob_key=blob_key_for(pdf.sha256),
            source_url=url,
            kind="transcript",
            period=period,
        )
        await db.commit()
    return result


async def sign_in(make_user: MakeUser, session_factory: Factory) -> tuple[UUID, dict[str, str]]:
    user_id = await make_user()
    token = await open_session(session_factory, user_id)
    return user_id, {"Cookie": f"session={token}"}


async def rows(engine: AsyncEngine, sql: str) -> list[tuple[Any, ...]]:
    async with engine.connect() as connection:
        return [tuple(row) for row in await connection.execute(text(sql))]


async def counts(engine: AsyncEngine) -> tuple[int, int]:
    documents = await rows(engine, "SELECT count(*) FROM documents")
    jobs = await rows(engine, "SELECT count(*) FROM jobs")
    return documents[0][0], jobs[0][0]


# --- recording a document -------------------------------------------------------------------------


async def test_recording_a_filing_creates_the_document_and_queues_one_job(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    view, created = await record(session_factory)

    assert created
    assert view.symbol == "TCS"
    assert view.status == "pending"
    assert view.size_bytes == len(DATA)
    assert view.page_count is None
    assert (view.source, view.source_url) == ("bse", bse_url(1))
    assert (view.kind, view.period) == ("transcript", "Jul 2026")

    sha = pdf_of().sha256
    [document] = await rows(
        admin_engine, "SELECT id, sha256, uploaded_by, status, blob_key FROM documents"
    )
    assert document == (view.id, sha, None, "pending", f"documents/{sha}.pdf")

    # Queued in the SAME transaction as the document (ADR 005): there is never a document that no
    # job will ever process.
    [job] = await rows(admin_engine, "SELECT kind, payload, dedupe_key, status FROM jobs")
    assert job == (
        "ingest_document",
        {"document_id": view.id},
        f"ingest_document:{view.id}",
        "pending",
    )


async def test_the_same_file_again_returns_the_existing_document(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    first, first_created = await record(session_factory, url=bse_url(1))
    second, second_created = await record(session_factory, url=bse_url(2), period="Other")

    assert (first_created, second_created) == (True, False)
    assert second == first  # including the ORIGINAL address and period
    assert await counts(admin_engine) == (1, 1)


async def test_eight_concurrent_recordings_of_one_file_give_one_document_and_one_job(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """The P9 acceptance race: eight transactions insert the same bytes (under eight addresses, as
    when BSE lists one file several times); the unique constraint and ON CONFLICT decide which one
    creates the row.

    Every call must SUCCEED, not merely leave the right rows behind. The first version of the
    table had a second unique constraint (on blob_key); ON CONFLICT (sha256) does not cover it, so
    under this race some callers failed while the data stayed correct."""
    results = await asyncio.gather(*(record(session_factory, url=bse_url(n)) for n in range(8)))

    assert sorted(created for _, created in results) == [False] * 7 + [True]
    assert len({view.id for view, _ in results}) == 1
    assert await counts(admin_engine) == (1, 1)


# --- reading over HTTP ----------------------------------------------------------------------------


async def test_a_document_can_be_read_back_and_is_listed_under_its_stock(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    view, _ = await record(session_factory)
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        one = await client.get(f"/api/v1/documents/{view.id}", headers=cookie)
        tcs = await client.get("/api/v1/stocks/TCS/documents", headers=cookie)
        reliance = await client.get("/api/v1/stocks/RELIANCE/documents", headers=cookie)

    assert one.status_code == 200
    body = one.json()
    assert set(body) == {
        "id", "symbol", "title", "status", "size_bytes", "page_count", "failure_reason",
        "created_at", "source", "source_url", "kind", "period",
    }  # fmt: skip
    assert (body["id"], body["source_url"], body["period"]) == (view.id, bse_url(1), "Jul 2026")
    assert "blob_key" not in body  # where the copy is stored stays inside
    assert one.headers["cache-control"] == "no-store"
    assert tcs.json() == {"items": [body], "next_cursor": None}
    assert reliance.json() == {"items": [], "next_cursor": None}


async def test_listing_an_unknown_stock_is_404(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        response = await client.get("/api/v1/stocks/INFY/documents", headers=cookie)
    assert response.status_code == 404
    assert "INFY" not in response.text  # the error never repeats what the caller sent


async def test_an_unknown_document_is_404(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        response = await client.get("/api/v1/documents/999999", headers=cookie)
    assert response.status_code == 404


async def test_a_signed_in_user_still_cannot_upload(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        response = await client.post(
            "/api/v1/stocks/TCS/documents?title=Q2",
            content=DATA,
            headers={
                **cookie,
                "Origin": "http://localhost:8000",
                "Content-Type": "application/pdf",
            },
        )

    assert response.status_code == 405
    assert await counts(admin_engine) == (0, 0)


async def test_the_list_is_newest_first_and_pages_with_a_cursor(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    ids = []
    for number in range(5):
        view, _ = await record(
            session_factory, data=DATA + str(number).encode(), url=bse_url(number)
        )
        ids.append(view.id)
    _, cookie = await sign_in(make_user, session_factory)

    async with running_app(db_config.settings()) as (_, client):
        first = (await client.get("/api/v1/stocks/TCS/documents?limit=2", headers=cookie)).json()
        second = (
            await client.get(
                f"/api/v1/stocks/TCS/documents?limit=2&cursor={first['next_cursor']}",
                headers=cookie,
            )
        ).json()
        third = (
            await client.get(
                f"/api/v1/stocks/TCS/documents?limit=2&cursor={second['next_cursor']}",
                headers=cookie,
            )
        ).json()

    newest_first = list(reversed(ids))
    assert [d["id"] for d in first["items"]] == newest_first[:2]
    assert [d["id"] for d in second["items"]] == newest_first[2:4]
    assert [d["id"] for d in third["items"]] == newest_first[4:]
    assert third["next_cursor"] is None
