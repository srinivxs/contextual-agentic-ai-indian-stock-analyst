"""The worker ingesting documents end to end: stored file -> page text -> chunks, real database.

The file comes from a temporary folder and the PDFs are synthetic (tests/pdfs.py). What is checked:
the rows a successful run leaves, the clear status a bad document gets, that transient failures are
retried and permanent ones are not, and that running a job twice (or two workers at once) never
duplicates anything.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.blobs import FilesystemBlobStore, blob_key_for
from app.documents import Upload, read_pdf, record_upload
from app.jobs import claim_next
from app.worker import WorkerContext, handle, run_forever, run_once
from tests.integration.conftest import MakeUser
from tests.pdfs import CORRUPT, DEMOCO_RESULTS, SCANNED

pytestmark = pytest.mark.usefixtures("migrated_db", "clean_document_tables")

Factory = async_sessionmaker[AsyncSession]


async def one_chunk(data: bytes) -> Any:
    yield data


async def seed(
    data: bytes, factory: Factory, store: FilesystemBlobStore, make_user: MakeUser
) -> int:
    """Exactly what the upload endpoint does: store the file, then record it with its job."""
    upload: Upload = await read_pdf(one_chunk(data), limit=10_000_000)
    key = blob_key_for(upload.sha256)
    await store.put(key, upload.data)
    user_id = await make_user()
    async with factory() as db:
        stock = (await db.execute(text("SELECT id FROM stocks WHERE symbol = 'TCS'"))).scalar_one()
        document, _ = await record_upload(
            db, stock=stock, user_id=user_id, title="DemoCo results", upload=upload, blob_key=key
        )
        await db.commit()
    return document.id


@pytest.fixture
def store(tmp_path: Path) -> FilesystemBlobStore:
    return FilesystemBlobStore(tmp_path / "blobs")


@pytest.fixture
def context(session_factory: Factory, store: FilesystemBlobStore) -> WorkerContext:
    return WorkerContext(session_factory=session_factory, blob_store=store, lease_seconds=300)


async def document(engine: AsyncEngine, document_id: int) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT status, page_count, failure_reason FROM documents WHERE id = :id"),
                {"id": document_id},
            )
        ).one()
        return dict(row._mapping)


async def rows(engine: AsyncEngine, sql: str) -> list[tuple[Any, ...]]:
    async with engine.connect() as connection:
        return [tuple(row) for row in await connection.execute(text(sql))]


# --- the good path --------------------------------------------------------------------------------


async def test_a_text_pdf_becomes_pages_and_page_numbered_chunks(
    context: WorkerContext,
    session_factory: Factory,
    store: FilesystemBlobStore,
    make_user: MakeUser,
    admin_engine: AsyncEngine,
) -> None:
    document_id = await seed(DEMOCO_RESULTS, session_factory, store, make_user)

    assert await run_once(context) is True

    assert await document(admin_engine, document_id) == {
        "status": "completed",
        "page_count": 2,
        "failure_reason": None,
    }
    pages = await rows(admin_engine, "SELECT page_number, text FROM document_pages ORDER BY 1")
    assert [p[0] for p in pages] == [1, 2]
    assert "Rs 1,234 crore" in pages[0][1]
    chunks = await rows(
        admin_engine, "SELECT ordinal, page_number, text FROM chunks ORDER BY ordinal"
    )
    assert [(c[0], c[1]) for c in chunks] == [(0, 1), (1, 2)]
    assert "widgets segment" in chunks[1][2]
    assert await rows(admin_engine, "SELECT status FROM jobs") == [("completed",)]


async def test_nothing_queued_means_nothing_done(context: WorkerContext) -> None:
    assert await run_once(context) is False


async def test_running_the_same_ingestion_again_changes_nothing(
    context: WorkerContext,
    session_factory: Factory,
    store: FilesystemBlobStore,
    make_user: MakeUser,
    admin_engine: AsyncEngine,
) -> None:
    """Idempotent: a second run replaces the pages and chunks with identical ones."""
    document_id = await seed(DEMOCO_RESULTS, session_factory, store, make_user)
    await run_once(context)
    first = await rows(admin_engine, "SELECT ordinal, page_number, content_hash FROM chunks")

    async with admin_engine.begin() as connection:  # queue the same work again
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('ingest_document', "
                "jsonb_build_object('document_id', CAST(:id AS bigint)), :key)"
            ),
            {"id": document_id, "key": f"ingest_document:{document_id}"},
        )
    assert await run_once(context) is True

    assert await rows(admin_engine, "SELECT ordinal, page_number, content_hash FROM chunks") == (
        first
    )
    assert (await rows(admin_engine, "SELECT count(*) FROM document_pages"))[0][0] == 2
    # The re-run must have SUCCEEDED, not merely left the old rows alone after failing.
    assert await rows(admin_engine, "SELECT status FROM jobs ORDER BY id") == [
        ("completed",),
        ("completed",),
    ]


async def test_two_workers_at_once_process_the_document_exactly_once(
    session_factory: Factory,
    store: FilesystemBlobStore,
    make_user: MakeUser,
    admin_engine: AsyncEngine,
) -> None:
    await seed(DEMOCO_RESULTS, session_factory, store, make_user)
    workers = [WorkerContext(session_factory, store, lease_seconds=300) for _ in range(2)]

    results = await asyncio.gather(*(run_once(w) for w in workers))

    assert sorted(results) == [False, True]
    assert (await rows(admin_engine, "SELECT count(*) FROM chunks"))[0][0] == 2


# --- documents that can never be ingested ---------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (SCANNED, "no text"),
        (CORRUPT, "could not be read"),
    ],
    ids=["scanned", "corrupt"],
)
async def test_a_document_that_can_never_work_fails_once_with_a_clear_reason(
    context: WorkerContext,
    session_factory: Factory,
    store: FilesystemBlobStore,
    make_user: MakeUser,
    admin_engine: AsyncEngine,
    data: bytes,
    reason: str,
) -> None:
    """Retrying cannot turn a scan into text, so the job fails at once instead of three times."""
    document_id = await seed(data, session_factory, store, make_user)

    await run_once(context)

    state = await document(admin_engine, document_id)
    assert state["status"] == "failed"
    assert reason in state["failure_reason"].lower()
    assert await rows(admin_engine, "SELECT status, attempts FROM jobs") == [("failed", 1)]
    assert await rows(admin_engine, "SELECT count(*) FROM chunks") == [(0,)]
    assert await rows(admin_engine, "SELECT count(*) FROM document_pages") == [(0,)]


async def test_a_job_for_a_document_that_no_longer_exists_fails_without_retrying(
    context: WorkerContext, admin_engine: AsyncEngine
) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, payload, dedupe_key) VALUES "
                "('ingest_document', '{\"document_id\": 424242}'::jsonb, 'ingest_document:424242')"
            )
        )
    await run_once(context)
    assert await rows(admin_engine, "SELECT status, attempts FROM jobs") == [("failed", 1)]


async def test_a_kind_of_job_this_worker_cannot_do_fails_without_retrying(
    context: WorkerContext, admin_engine: AsyncEngine
) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO jobs (kind, dedupe_key) VALUES ('poll_feed', 'poll_feed:rbi')")
        )
    await run_once(context)
    [(status, error)] = await rows(admin_engine, "SELECT status, last_error FROM jobs")
    assert status == "failed"
    assert "poll_feed" in error


# --- failures that may pass -----------------------------------------------------------------------


class FlakyStore(FilesystemBlobStore):
    async def get(self, key: str) -> bytes:
        raise OSError("the store did not answer")


async def test_a_transient_failure_is_retried_and_the_document_waits(
    session_factory: Factory,
    store: FilesystemBlobStore,
    make_user: MakeUser,
    admin_engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    document_id = await seed(DEMOCO_RESULTS, session_factory, store, make_user)
    flaky = WorkerContext(session_factory, FlakyStore(tmp_path / "blobs"), lease_seconds=300)

    await run_once(flaky)

    assert (await document(admin_engine, document_id))["status"] == "pending"
    [(status, attempts, error)] = await rows(
        admin_engine, "SELECT status, attempts, last_error FROM jobs"
    )
    assert (status, attempts) == ("pending", 1)
    assert "OSError" in error


async def test_after_the_last_attempt_the_document_fails_with_a_plain_reason(
    session_factory: Factory,
    store: FilesystemBlobStore,
    make_user: MakeUser,
    admin_engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    document_id = await seed(DEMOCO_RESULTS, session_factory, store, make_user)
    async with admin_engine.begin() as connection:
        await connection.execute(text("UPDATE jobs SET max_attempts = 1"))
    flaky = WorkerContext(session_factory, FlakyStore(tmp_path / "blobs"), lease_seconds=300)

    await run_once(flaky)

    state = await document(admin_engine, document_id)
    assert state["status"] == "failed"
    # Users see a plain sentence; the exception text stays in the job row and the log.
    assert "OSError" not in state["failure_reason"]
    assert "try uploading it again" in state["failure_reason"]


async def test_a_worker_that_lost_its_lease_does_not_write_results(
    context: WorkerContext,
    session_factory: Factory,
    store: FilesystemBlobStore,
    make_user: MakeUser,
    admin_engine: AsyncEngine,
) -> None:
    """The fencing check runs inside the final transaction, before any row is written."""
    document_id = await seed(DEMOCO_RESULTS, session_factory, store, make_user)
    async with session_factory() as db:
        stale = await claim_next(db, lease_seconds=300)
        await db.commit()
    assert stale is not None
    async with admin_engine.begin() as connection:  # someone else has since taken the job over
        await connection.execute(text("UPDATE jobs SET attempts = attempts + 1"))

    await handle(context, stale)

    assert (await document(admin_engine, document_id))["status"] != "completed"
    assert await rows(admin_engine, "SELECT count(*) FROM chunks") == [(0,)]


# --- the loop -------------------------------------------------------------------------------------


async def test_the_loop_works_through_the_queue_and_stops_when_asked(
    session_factory: Factory,
    store: FilesystemBlobStore,
    make_user: MakeUser,
    admin_engine: AsyncEngine,
) -> None:
    await seed(DEMOCO_RESULTS, session_factory, store, make_user)
    context = WorkerContext(session_factory, store, lease_seconds=300)
    stop = asyncio.Event()

    async def stop_when_done() -> None:
        for _ in range(200):
            if await rows(admin_engine, "SELECT status FROM jobs") == [("completed",)]:
                break
            await asyncio.sleep(0.05)
        stop.set()

    await asyncio.wait_for(
        asyncio.gather(run_forever(context, stop, poll_seconds=0.05), stop_when_done()),
        timeout=10,
    )
    assert stop.is_set()


async def test_the_loop_survives_a_database_outage_and_keeps_polling() -> None:
    """A worker must not exit because the database blinked; it logs, waits and tries again."""
    calls = 0
    stop = asyncio.Event()

    def broken_session() -> Any:
        nonlocal calls
        calls += 1
        if calls >= 3:
            stop.set()
        raise ConnectionError("database unavailable")

    context = WorkerContext(broken_session, FilesystemBlobStore(Path("unused")), 300)  # type: ignore[arg-type]
    await asyncio.wait_for(run_forever(context, stop, poll_seconds=0.01), timeout=5)
    assert calls >= 3


# --- more ways to lose or break a job -------------------------------------------------------------


async def test_a_job_without_a_document_id_fails_without_retrying(
    context: WorkerContext, admin_engine: AsyncEngine
) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, dedupe_key) "
                "VALUES ('ingest_document', 'ingest_document:x')"
            )
        )
    await run_once(context)
    [(status, attempts, error)] = await rows(
        admin_engine, "SELECT status, attempts, last_error FROM jobs"
    )
    assert (status, attempts) == ("failed", 1)
    assert "document_id" in error


class StealingStore(FilesystemBlobStore):
    """Reads the file, and meanwhile another worker takes the job over (the lease ran out)."""

    def __init__(self, root: Path, engine: AsyncEngine) -> None:
        super().__init__(root)
        self.engine = engine

    async def get(self, key: str) -> bytes:
        async with self.engine.begin() as connection:
            await connection.execute(text("UPDATE jobs SET attempts = attempts + 1"))
        return await super().get(key)


async def test_a_worker_that_loses_its_lease_mid_job_writes_nothing(
    session_factory: Factory,
    store: FilesystemBlobStore,
    make_user: MakeUser,
    admin_engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    """The check at the start of the final transaction is what stops a late writer."""
    document_id = await seed(DEMOCO_RESULTS, session_factory, store, make_user)
    slow = WorkerContext(session_factory, StealingStore(tmp_path / "blobs", admin_engine), 300)

    await run_once(slow)

    assert (await document(admin_engine, document_id))["status"] == "processing"
    assert await rows(admin_engine, "SELECT count(*) FROM chunks") == [(0,)]
    assert await rows(admin_engine, "SELECT status FROM jobs") == [("processing",)]


@pytest.mark.parametrize("store_kind", ["rejecting", "failing"])
async def test_a_stale_worker_records_no_outcome_for_a_job_it_no_longer_holds(
    session_factory: Factory,
    store: FilesystemBlobStore,
    make_user: MakeUser,
    admin_engine: AsyncEngine,
    tmp_path: Path,
    store_kind: str,
) -> None:
    """Neither a rejection nor a retry may be written by a worker whose claim was superseded."""
    data = SCANNED if store_kind == "rejecting" else DEMOCO_RESULTS
    document_id = await seed(data, session_factory, store, make_user)
    blob_store = (
        StealingStore(tmp_path / "blobs", admin_engine)
        if store_kind == "rejecting"
        else FlakyStealingStore(tmp_path / "blobs", admin_engine)
    )

    await run_once(WorkerContext(session_factory, blob_store, 300))

    assert (await document(admin_engine, document_id))["status"] == "processing"
    assert await rows(admin_engine, "SELECT status FROM jobs") == [("processing",)]


class FlakyStealingStore(StealingStore):
    async def get(self, key: str) -> bytes:
        await super().get(key)
        raise OSError("the store did not answer")


async def test_only_document_jobs_touch_a_document(
    session_factory: Factory,
    store: FilesystemBlobStore,
    make_user: MakeUser,
    admin_engine: AsyncEngine,
) -> None:
    """A feed job (P10+) that fails must not rewrite the status of some document."""
    from app.jobs import ClaimedJob
    from app.worker import mark_job_document

    document_id = await seed(DEMOCO_RESULTS, session_factory, store, make_user)
    feed_job = ClaimedJob(
        id=1, kind="poll_feed", payload={"document_id": document_id}, attempts=1, max_attempts=3
    )
    async with session_factory() as db:
        await mark_job_document(db, feed_job, "failed", "should not appear")
        await db.commit()

    assert (await document(admin_engine, document_id))["status"] == "pending"
