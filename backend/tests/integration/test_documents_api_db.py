"""Uploading and listing documents over HTTP, against the real database and a temporary folder.

The P9 acceptance rule is the heart of this file: the same file uploaded twice, or eight times at
once, gives ONE document and ONE ingestion job. It holds because the file is stored under its own
SHA-256 (identical bytes, identical place) and the database has a unique constraint on that hash
plus a partial unique index on the job's dedupe key. No application-level lock is involved.
"""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from tests.helpers import running_app
from tests.integration.auth_helpers import open_session
from tests.integration.conftest import DbConfig, MakeUser

pytestmark = pytest.mark.usefixtures("clean_document_tables")

Factory = async_sessionmaker[AsyncSession]
ORIGIN = "http://localhost:8000"
PDF = b"%PDF-1.7\n" + b"fictional DemoCo quarterly results " * 200
PDF_SHA = hashlib.sha256(PDF).hexdigest()


def upload_url(symbol: str = "TCS", title: str = "Q2 results") -> str:
    return f"/api/v1/stocks/{symbol}/documents?title={title}"


def headers(cookie: dict[str, str], content_type: str = "application/pdf") -> dict[str, str]:
    return {**cookie, "Origin": ORIGIN, "Content-Type": content_type}


async def sign_in(make_user: MakeUser, session_factory: Factory) -> tuple[UUID, dict[str, str]]:
    user_id = await make_user()
    token = await open_session(session_factory, user_id)
    return user_id, {"Cookie": f"session={token}"}


def settings_with(db_config: DbConfig, tmp_path: Path, **overrides: Any) -> Settings:
    return db_config.settings(blob_root=tmp_path / "blobs", **overrides)


async def rows(engine: AsyncEngine, sql: str) -> list[tuple[Any, ...]]:
    async with engine.connect() as connection:
        return [tuple(row) for row in await connection.execute(text(sql))]


async def counts(engine: AsyncEngine) -> tuple[int, int]:
    documents = await rows(engine, "SELECT count(*) FROM documents")
    jobs = await rows(engine, "SELECT count(*) FROM jobs")
    return documents[0][0], jobs[0][0]


def stored_files(tmp_path: Path) -> list[Path]:
    folder = tmp_path / "blobs"
    return sorted(p for p in folder.rglob("*") if p.is_file()) if folder.exists() else []


# --- a first upload -------------------------------------------------------------------------------


async def test_an_upload_stores_the_file_records_the_document_and_queues_one_job(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    user_id, cookie = await sign_in(make_user, session_factory)
    async with running_app(settings_with(db_config, tmp_path)) as (_, client):
        response = await client.post(upload_url(), content=PDF, headers=headers(cookie))

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "id", "symbol", "title", "status", "size_bytes", "page_count", "failure_reason",
        "created_at", "source", "source_url", "kind", "period",
    }  # fmt: skip
    assert body["symbol"] == "TCS"
    assert body["title"] == "Q2 results"
    assert body["status"] == "pending"
    assert body["size_bytes"] == len(PDF)
    assert body["page_count"] is None
    assert body["failure_reason"] is None
    assert body["source"] == "upload"
    assert body["source_url"] is None  # an upload has no public address to link to
    assert body["kind"] is None  # nor a kind or period of its own
    assert body["period"] is None

    # The file, under its own hash: nothing about the client's filename reaches the disk.
    assert stored_files(tmp_path) == [tmp_path / "blobs" / "documents" / f"{PDF_SHA}.pdf"]
    assert stored_files(tmp_path)[0].read_bytes() == PDF

    [document] = await rows(
        admin_engine, "SELECT id, sha256, uploaded_by, status, blob_key FROM documents"
    )
    assert document == (body["id"], PDF_SHA, user_id, "pending", f"documents/{PDF_SHA}.pdf")

    # Queued in the SAME transaction as the document (ADR 005): there is never a document that no
    # job will ever process.
    [job] = await rows(admin_engine, "SELECT kind, payload, dedupe_key, status FROM jobs")
    assert job == (
        "ingest_document",
        {"document_id": body["id"]},
        f"ingest_document:{body['id']}",
        "pending",
    )


async def test_the_new_document_can_be_read_back_and_is_listed_under_its_stock(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, tmp_path: Path
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(settings_with(db_config, tmp_path)) as (_, client):
        created = (await client.post(upload_url(), content=PDF, headers=headers(cookie))).json()
        one = await client.get(f"/api/v1/documents/{created['id']}", headers=cookie)
        tcs = await client.get("/api/v1/stocks/TCS/documents", headers=cookie)
        reliance = await client.get("/api/v1/stocks/RELIANCE/documents", headers=cookie)

    assert one.status_code == 200
    assert one.json() == created
    assert one.headers["cache-control"] == "no-store"
    assert tcs.json() == {"items": [created], "next_cursor": None}
    assert reliance.json() == {"items": [], "next_cursor": None}


# --- the same file again --------------------------------------------------------------------------


async def test_uploading_the_same_file_again_returns_the_existing_document(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(settings_with(db_config, tmp_path)) as (_, client):
        first = await client.post(upload_url(), content=PDF, headers=headers(cookie))
        second = await client.post(
            upload_url(title="A different title"), content=PDF, headers=headers(cookie)
        )

    assert first.status_code == 201
    assert second.status_code == 200  # nothing was created
    assert second.json() == first.json()  # including the ORIGINAL title
    assert await counts(admin_engine) == (1, 1)


async def test_eight_concurrent_uploads_of_one_file_give_one_document_and_one_job(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    """The P9 acceptance race. Each request hashes, stores and inserts on its own; the database's
    unique constraint and ON CONFLICT decide which one creates the row.

    Every request must SUCCEED, not merely leave the right rows behind. The first version of the
    table had a second unique constraint (on blob_key); ON CONFLICT (sha256) does not cover it, so
    under this race some requests failed with a 500 while the data stayed correct."""
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(settings_with(db_config, tmp_path)) as (_, client):
        responses = await asyncio.gather(
            *(client.post(upload_url(), content=PDF, headers=headers(cookie)) for _ in range(8))
        )

    assert sorted(r.status_code for r in responses) == [200] * 7 + [201]
    assert len({r.json()["id"] for r in responses}) == 1
    assert await counts(admin_engine) == (1, 1)
    assert len(stored_files(tmp_path)) == 1


async def test_the_same_file_under_another_stock_is_a_conflict(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    """One file is one document, which belongs to one stock. A TCS report is not a RELIANCE one."""
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(settings_with(db_config, tmp_path)) as (_, client):
        await client.post(upload_url("TCS"), content=PDF, headers=headers(cookie))
        response = await client.post(upload_url("RELIANCE"), content=PDF, headers=headers(cookie))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert "TCS" in response.json()["error"]["message"]
    assert await counts(admin_engine) == (1, 1)


# --- what is refused, and that nothing is kept when it is -----------------------------------------


@pytest.mark.parametrize(
    ("content", "content_type", "status", "code"),
    [
        (b"PK\x03\x04 a zip file", "application/pdf", 415, "unsupported_media_type"),
        (PDF, "text/plain", 415, "unsupported_media_type"),
        (b"", "application/pdf", 415, "unsupported_media_type"),
    ],
    ids=["not-a-pdf", "wrong-content-type", "empty"],
)
async def test_something_that_is_not_a_pdf_is_refused_and_nothing_is_stored(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    tmp_path: Path,
    content: bytes,
    content_type: str,
    status: int,
    code: str,
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(settings_with(db_config, tmp_path)) as (_, client):
        response = await client.post(
            upload_url(), content=content, headers=headers(cookie, content_type)
        )

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert await counts(admin_engine) == (0, 0)
    assert stored_files(tmp_path) == []


async def test_a_file_over_the_limit_is_refused_and_nothing_is_stored(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    settings = settings_with(db_config, tmp_path, upload_max_bytes=len(PDF) - 1)
    async with running_app(settings) as (_, client):
        response = await client.post(upload_url(), content=PDF, headers=headers(cookie))

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert await counts(admin_engine) == (0, 0)
    assert stored_files(tmp_path) == []


async def test_a_client_that_hides_the_size_is_still_cut_off_at_the_limit(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    """No Content-Length (a chunked upload): the early check has nothing to read, so the limit must
    be enforced while streaming. Otherwise a client could send gigabytes by simply not saying so."""
    _, cookie = await sign_in(make_user, session_factory)

    async def chunked() -> AsyncIterator[bytes]:
        for start in range(0, len(PDF), 1000):
            yield PDF[start : start + 1000]

    settings = settings_with(db_config, tmp_path, upload_max_bytes=len(PDF) - 1)
    async with running_app(settings) as (_, client):
        response = await client.post(upload_url(), content=chunked(), headers=headers(cookie))

    assert response.request.headers.get("content-length") is None  # the premise of this test
    assert response.status_code == 413
    assert await counts(admin_engine) == (0, 0)
    assert stored_files(tmp_path) == []


async def test_an_unknown_stock_is_404_and_the_body_is_never_stored(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(settings_with(db_config, tmp_path)) as (_, client):
        response = await client.post(upload_url("INFY"), content=PDF, headers=headers(cookie))

    assert response.status_code == 404
    assert "INFY" not in response.text  # the error never repeats what the caller sent
    assert await counts(admin_engine) == (0, 0)
    assert stored_files(tmp_path) == []


@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/stocks/TCS/documents",
        "/api/v1/stocks/TCS/documents?title=",
        "/api/v1/stocks/TCS/documents?title=%20%20",
        "/api/v1/stocks/TCS/documents?title=" + "x" * 201,
        "/api/v1/stocks/tcs/documents?title=ok",
    ],
    ids=["no-title", "empty-title", "blank-title", "long-title", "lower-case-symbol"],
)
async def test_invalid_parameters_are_422_and_nothing_is_stored(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    tmp_path: Path,
    url: str,
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(settings_with(db_config, tmp_path)) as (_, client):
        response = await client.post(url, content=PDF, headers=headers(cookie))

    assert response.status_code == 422
    assert await counts(admin_engine) == (0, 0)
    assert stored_files(tmp_path) == []


# --- reading --------------------------------------------------------------------------------------


async def test_listing_an_unknown_stock_is_404(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, tmp_path: Path
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(settings_with(db_config, tmp_path)) as (_, client):
        response = await client.get("/api/v1/stocks/INFY/documents", headers=cookie)
    assert response.status_code == 404
    assert "INFY" not in response.text


async def test_an_unknown_document_is_404(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, tmp_path: Path
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(settings_with(db_config, tmp_path)) as (_, client):
        response = await client.get("/api/v1/documents/999999", headers=cookie)
    assert response.status_code == 404


async def test_the_list_is_newest_first_and_pages_with_a_cursor(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, tmp_path: Path
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(settings_with(db_config, tmp_path)) as (_, client):
        ids = []
        for number in range(5):
            pdf = PDF + str(number).encode()
            created = await client.post(
                upload_url(title=f"Report {number}"), content=pdf, headers=headers(cookie)
            )
            ids.append(created.json()["id"])

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
