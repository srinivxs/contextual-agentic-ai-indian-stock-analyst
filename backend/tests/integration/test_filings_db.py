"""Automatic filings (ADR 018), end to end in the real database, against a fake internet.

    worker timer -> discover_filings job -> screener.in page -> official BSE links
                 -> one fetch_filing job per new link -> BSE PDF -> document + ingest job -> P9b

The internet is httpx.MockTransport: a synthetic screener page (tests/filings_html.py) and small
synthetic PDFs. Nothing here talks to screener.in or bseindia.com.
"""

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.blobs import FilesystemBlobStore, blob_key_for
from app.documents import read_pdf, record_upload
from app.filings import SCREENER_URL, enqueue_discovery
from app.worker import WorkerContext, run_forever, run_once
from tests.filings_html import DEMOCO_PAGE, EXPECTED, TODAY, file_url, transcript_url
from tests.pdfs import make_pdf

pytestmark = pytest.mark.usefixtures("migrated_db", "clean_document_tables")

Factory = async_sessionmaker[AsyncSession]


def pdf_for(url: str) -> bytes:
    """A distinct synthetic PDF per URL, with enough text to be ingested."""
    return make_pdf([[f"DemoCo Limited (fictional) filing at {url[-45:]}", "Revenue rose. " * 10]])


async def one_chunk(data: bytes) -> AsyncIterator[bytes]:
    yield data


class FakeInternet:
    def __init__(self) -> None:
        self.requests: list[str] = []
        self.pages: dict[str, httpx.Response] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url in self.pages:
            return self.pages[url]
        if url.startswith(SCREENER_URL.format(symbol="TCS")):
            return httpx.Response(200, text=DEMOCO_PAGE)
        if "/stockinfo/AnnPdfOpen.aspx" in url:
            return httpx.Response(406)  # what BSE's script page often answered in the real run
        if url.startswith("https://www.bseindia.com/xml-data/corpfiling/AttachHis/"):
            return httpx.Response(200, content=pdf_for(url))
        return httpx.Response(404)


@pytest.fixture
def internet() -> FakeInternet:
    return FakeInternet()


@pytest.fixture
async def context(
    session_factory: Factory, tmp_path: Path, internet: FakeInternet
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


async def rows(engine: AsyncEngine, sql: str) -> list[tuple[Any, ...]]:
    async with engine.connect() as connection:
        return [tuple(row) for row in await connection.execute(text(sql))]


async def enqueue_discover(engine: AsyncEngine, symbol: str = "TCS") -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('discover_filings', "
                "jsonb_build_object('symbol', CAST(:symbol AS text)), :key)"
            ),
            {"symbol": symbol, "key": f"discover_filings:{symbol}"},
        )


async def drain(context: WorkerContext, limit: int = 50) -> int:
    done = 0
    while done < limit and await run_once(context):
        done += 1
    return done


# --- the whole chain ------------------------------------------------------------------------------


async def test_discovery_queues_one_fetch_per_chosen_filing(
    context: WorkerContext, internet: FakeInternet, admin_engine: AsyncEngine
) -> None:
    await enqueue_discover(admin_engine)

    assert await run_once(context) is True

    assert internet.requests == [SCREENER_URL.format(symbol="TCS")]  # one page, nothing else
    fetches = await rows(
        admin_engine,
        "SELECT payload->>'url', payload->>'kind', payload->>'label' FROM jobs "
        "WHERE kind = 'fetch_filing' ORDER BY id",
    )
    assert fetches == [(url, kind, label) for kind, label, url in EXPECTED]
    assert await rows(admin_engine, "SELECT status FROM jobs WHERE kind = 'discover_filings'") == [
        ("completed",)
    ]


async def test_the_chain_ends_in_ingested_official_documents(
    context: WorkerContext, internet: FakeInternet, admin_engine: AsyncEngine
) -> None:
    await enqueue_discover(admin_engine)

    await drain(context)

    documents = await rows(
        admin_engine,
        "SELECT title, source, source_url, uploaded_by, status FROM documents ORDER BY id",
    )
    assert len(documents) == len(EXPECTED)
    assert {d[1] for d in documents} == {"bse"}
    assert {d[2] for d in documents} == {url for _, _, url in EXPECTED}
    assert {d[3] for d in documents} == {None}  # nobody uploaded these
    assert {d[4] for d in documents} == {"completed"}
    assert ("TCS earnings call transcript, Jul 2026",) in [(d[0],) for d in documents]
    assert await rows(admin_engine, "SELECT DISTINCT status FROM jobs") == [("completed",)]
    # Every document was fetched from BSE exactly once.
    assert sorted(internet.requests[1:]) == sorted(url for _, _, url in EXPECTED)


async def test_a_second_discovery_fetches_nothing_already_known(
    context: WorkerContext, internet: FakeInternet, admin_engine: AsyncEngine
) -> None:
    await enqueue_discover(admin_engine)
    await drain(context)
    before = len(internet.requests)

    async with admin_engine.begin() as connection:  # tomorrow's run
        await connection.execute(text("DELETE FROM jobs"))
    await enqueue_discover(admin_engine)
    await drain(context)

    assert internet.requests[before:] == [SCREENER_URL.format(symbol="TCS")]  # the page only
    assert await rows(admin_engine, "SELECT count(*) FROM documents") == [(len(EXPECTED),)]
    # And nothing was even queued: known addresses are skipped at discovery, not just at fetch.
    assert await rows(admin_engine, "SELECT kind FROM jobs") == [("discover_filings",)]


# --- what a fetch refuses -------------------------------------------------------------------------


async def insert_fetch(engine: AsyncEngine, url: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('fetch_filing', "
                "jsonb_build_object('symbol', 'TCS', 'url', CAST(:url AS text), "
                "'kind', 'transcript', 'label', 'Jul 2026'), :key)"
            ),
            {"url": url, "key": "fetch_filing:" + hashlib.sha256(url.encode()).hexdigest()},
        )


async def test_a_fetch_for_an_address_that_is_not_an_official_filing_is_refused(
    context: WorkerContext, internet: FakeInternet, admin_engine: AsyncEngine
) -> None:
    """Even a job row tampered with directly cannot make the worker fetch an arbitrary URL."""
    await insert_fetch(admin_engine, "http://169.254.169.254/latest/meta-data")

    await run_once(context)

    assert internet.requests == []
    [(status, attempts, error)] = await rows(
        admin_engine, "SELECT status, attempts, last_error FROM jobs"
    )
    assert (status, attempts) == ("failed", 1)
    # Refused by the job's own check, before any database or network work. polite_get would
    # refuse it too (a second layer, tested in tests/unit/test_polite_fetch.py).
    assert error == "not an official filing address"


async def test_a_filing_that_is_not_a_pdf_is_refused_without_retrying(
    context: WorkerContext, internet: FakeInternet, admin_engine: AsyncEngine
) -> None:
    url = transcript_url(1)
    internet.pages[file_url(1)] = httpx.Response(200, text="<html>maintenance</html>")
    await insert_fetch(admin_engine, url)

    await run_once(context)

    assert await rows(admin_engine, "SELECT count(*) FROM documents") == [(0,)]
    [(status, error)] = await rows(admin_engine, "SELECT status, last_error FROM jobs")
    assert status == "failed"
    assert "not a PDF" in error


async def test_a_bse_outage_is_retried_later(
    context: WorkerContext, internet: FakeInternet, admin_engine: AsyncEngine
) -> None:
    url = transcript_url(1)
    internet.pages[file_url(1)] = httpx.Response(503)
    await insert_fetch(admin_engine, url)

    await run_once(context)

    assert await rows(admin_engine, "SELECT status, attempts FROM jobs") == [("pending", 1)]


async def test_a_screener_outage_is_retried_later(
    context: WorkerContext, internet: FakeInternet, admin_engine: AsyncEngine
) -> None:
    internet.pages[SCREENER_URL.format(symbol="TCS")] = httpx.Response(429)
    await enqueue_discover(admin_engine)

    await run_once(context)

    assert await rows(admin_engine, "SELECT status FROM jobs") == [("pending",)]


async def test_a_filing_identical_to_an_uploaded_file_is_not_stored_twice(
    context: WorkerContext,
    internet: FakeInternet,
    admin_engine: AsyncEngine,
    session_factory: Factory,
) -> None:
    """Same bytes, same document: the sha256 rule from P9a covers both doors."""
    url = transcript_url(1)
    upload = await read_pdf(
        one_chunk(pdf_for(file_url(1))), limit=10_000_000
    )  # the bytes BSE serves
    await context.blob_store.put(blob_key_for(upload.sha256), upload.data)  # as the endpoint does
    async with session_factory() as db:  # someone uploaded this very file by hand earlier
        stock = (await db.execute(text("SELECT id FROM stocks WHERE symbol = 'TCS'"))).scalar_one()
        await record_upload(
            db,
            stock=stock,
            user_id=None,
            title="Uploaded by hand",
            upload=upload,
            blob_key=blob_key_for(upload.sha256),
        )
        await db.commit()
    await insert_fetch(admin_engine, url)

    await drain(context)  # the upload's own ingestion, then the fetch

    assert await rows(admin_engine, "SELECT title, source FROM documents") == [
        ("Uploaded by hand", "upload")
    ]
    assert await rows(admin_engine, "SELECT status FROM jobs WHERE kind = 'fetch_filing'") == [
        ("completed",)
    ]


async def test_discovery_for_an_unknown_stock_fails_without_retrying(
    context: WorkerContext, internet: FakeInternet, admin_engine: AsyncEngine
) -> None:
    await enqueue_discover(admin_engine, symbol="INFY")

    await run_once(context)

    assert internet.requests == []
    assert await rows(admin_engine, "SELECT status FROM jobs") == [("failed",)]


# --- the timer ------------------------------------------------------------------------------------


async def test_the_timer_queues_one_discovery_per_stock(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    async with session_factory() as db:
        queued = await enqueue_discovery(db, every_hours=24)
        await db.commit()

    assert queued == 3
    assert await rows(
        admin_engine,
        "SELECT payload->>'symbol' FROM jobs WHERE kind = 'discover_filings' ORDER BY 1",
    ) == [("HDFCBANK",), ("RELIANCE",), ("TCS",)]


async def test_the_timer_does_nothing_again_within_the_interval(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    for _ in range(3):
        async with session_factory() as db:
            await enqueue_discovery(db, every_hours=24)
            await db.commit()
    async with admin_engine.begin() as connection:  # even after the jobs have finished
        await connection.execute(text("UPDATE jobs SET status = 'completed'"))
    async with session_factory() as db:
        assert await enqueue_discovery(db, every_hours=24) == 0
        await db.commit()

    assert await rows(admin_engine, "SELECT count(*) FROM jobs") == [(3,)]


async def test_the_timer_queues_again_once_the_interval_has_passed(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    async with session_factory() as db:
        await enqueue_discovery(db, every_hours=24)
        await db.commit()
    async with admin_engine.begin() as connection:
        await connection.execute(
            text("UPDATE jobs SET status = 'completed', created_at = now() - interval '25 hours'")
        )

    async with session_factory() as db:
        assert await enqueue_discovery(db, every_hours=24) == 3
        await db.commit()


@pytest.mark.parametrize(("every_hours", "expected"), [(None, 0), (24, 3)])
async def test_the_loop_queues_discovery_only_when_switched_on(
    context: WorkerContext,
    admin_engine: AsyncEngine,
    every_hours: int | None,
    expected: int,
) -> None:
    """FILINGS_DISCOVERY off (the default) means the worker never reaches the internet itself."""
    import asyncio

    stop = asyncio.Event()
    stop.set()  # one pass of the loop, then stop
    await run_forever(context, stop, poll_seconds=0.01, discovery_every_hours=every_hours)

    assert await rows(
        admin_engine, "SELECT count(*) FROM jobs WHERE kind = 'discover_filings'"
    ) == [(expected,)]


# --- the less happy paths -------------------------------------------------------------------------


async def insert_job(engine: AsyncEngine, kind: str, payload: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, payload, dedupe_key) "
                "VALUES (:kind, CAST(:payload AS jsonb), :kind)"
            ),
            {"kind": kind, "payload": payload},
        )


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("fetch_filing", "{}"),
        ("fetch_filing", '{"symbol": "TCS", "url": 7, "kind": "transcript", "label": "x"}'),
        ("discover_filings", '{"symbol": ["TCS"]}'),
    ],
    ids=["missing-keys", "url-not-text", "symbol-not-text"],
)
async def test_a_malformed_filing_job_fails_without_retrying(
    context: WorkerContext,
    internet: FakeInternet,
    admin_engine: AsyncEngine,
    kind: str,
    payload: str,
) -> None:
    await insert_job(admin_engine, kind, payload)
    await run_once(context)
    assert internet.requests == []
    assert await rows(admin_engine, "SELECT status, attempts FROM jobs") == [("failed", 1)]


async def test_a_fetch_for_an_unknown_stock_fails_without_retrying(
    context: WorkerContext, internet: FakeInternet, admin_engine: AsyncEngine
) -> None:
    payload = (
        '{"symbol": "INFY", "url": "' + transcript_url(1) + '", '
        '"kind": "transcript", "label": "Jul 2026"}'
    )
    await insert_job(admin_engine, "fetch_filing", payload)
    await run_once(context)
    assert internet.requests == []
    assert await rows(admin_engine, "SELECT status FROM jobs") == [("failed",)]


async def test_a_filing_redirected_to_another_host_is_refused_for_good(
    context: WorkerContext, internet: FakeInternet, admin_engine: AsyncEngine
) -> None:
    url = transcript_url(1)
    internet.pages[file_url(1)] = httpx.Response(
        302, headers={"Location": "https://evil.example/x.pdf"}
    )
    await insert_fetch(admin_engine, url)

    await run_once(context)

    assert internet.requests == [file_url(1)]  # the redirect target was never requested
    assert await rows(admin_engine, "SELECT status FROM jobs") == [("failed",)]


async def test_a_filing_over_the_size_limit_is_refused_for_good(
    session_factory: Factory, tmp_path: Path, internet: FakeInternet, admin_engine: AsyncEngine
) -> None:
    await insert_fetch(admin_engine, transcript_url(1))
    async with httpx.AsyncClient(transport=httpx.MockTransport(internet)) as http:
        small = WorkerContext(
            session_factory, FilesystemBlobStore(tmp_path), 300, http=http, filings_max_bytes=100
        )
        await run_once(small)

    [(status, error)] = await rows(admin_engine, "SELECT status, last_error FROM jobs")
    assert status == "failed"
    assert "larger than 100 bytes" in error
    assert await rows(admin_engine, "SELECT count(*) FROM documents") == [(0,)]


async def test_a_fetch_for_an_address_already_stored_downloads_nothing(
    context: WorkerContext, internet: FakeInternet, admin_engine: AsyncEngine
) -> None:
    url = transcript_url(1)
    await insert_fetch(admin_engine, url)
    await drain(context)
    before = len(internet.requests)
    async with admin_engine.begin() as connection:
        await connection.execute(text("DELETE FROM jobs"))
    await insert_fetch(admin_engine, url)

    await run_once(context)

    assert len(internet.requests) == before
    assert await rows(admin_engine, "SELECT status FROM jobs") == [("completed",)]


@pytest.mark.parametrize("kind", ["discover_filings", "fetch_filing"])
async def test_with_the_switch_off_filing_jobs_fail_and_nothing_is_requested(
    session_factory: Factory, tmp_path: Path, admin_engine: AsyncEngine, kind: str
) -> None:
    await insert_job(admin_engine, kind, '{"symbol": "TCS"}')
    offline = WorkerContext(session_factory, FilesystemBlobStore(tmp_path), 300)  # no http

    await run_once(offline)

    [(status, error)] = await rows(admin_engine, "SELECT status, last_error FROM jobs")
    assert status == "failed"
    assert "switched off" in error


class StealingInternet(FakeInternet):
    """Answers, but first another worker takes the job over (the lease ran out meanwhile)."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__()
        self.engine = engine

    async def handle(self, request: httpx.Request) -> httpx.Response:
        async with self.engine.begin() as connection:
            await connection.execute(text("UPDATE jobs SET attempts = attempts + 1"))
        return self(request)


@pytest.mark.parametrize("which", ["discover", "fetch"])
async def test_a_worker_that_lost_its_lease_records_nothing(
    session_factory: Factory, tmp_path: Path, admin_engine: AsyncEngine, which: str
) -> None:
    internet = StealingInternet(admin_engine)
    if which == "discover":
        await enqueue_discover(admin_engine)
    else:
        await insert_fetch(admin_engine, transcript_url(1))
    async with httpx.AsyncClient(transport=httpx.MockTransport(internet.handle)) as http:
        context = WorkerContext(session_factory, FilesystemBlobStore(tmp_path), 300, http=http)
        await run_once(context)

    assert await rows(
        admin_engine, "SELECT count(*) FROM jobs WHERE kind = 'fetch_filing' AND status = 'pending'"
    ) == [(0,)]
    assert await rows(admin_engine, "SELECT count(*) FROM documents") == [(0,)]


async def test_a_database_error_while_queueing_discovery_does_not_stop_the_worker(
    tmp_path: Path,
) -> None:
    import asyncio

    calls = 0
    stop = asyncio.Event()

    def broken_session() -> Any:
        nonlocal calls
        calls += 1
        stop.set()
        raise ConnectionError("database unavailable")

    context = WorkerContext(broken_session, FilesystemBlobStore(tmp_path), 300)  # type: ignore[arg-type]
    await asyncio.wait_for(
        run_forever(context, stop, poll_seconds=0.01, discovery_every_hours=24), timeout=5
    )
    assert calls >= 1


async def test_a_second_pass_of_the_timer_queues_nothing_and_says_nothing(
    context: WorkerContext, admin_engine: AsyncEngine
) -> None:
    import asyncio

    for _ in range(2):
        stop = asyncio.Event()
        stop.set()
        await run_forever(context, stop, poll_seconds=0.01, discovery_every_hours=24)

    assert await rows(
        admin_engine, "SELECT count(*) FROM jobs WHERE kind = 'discover_filings'"
    ) == [(3,)]


async def test_a_filing_only_in_the_live_folder_is_found_there(
    context: WorkerContext, internet: FakeInternet, admin_engine: AsyncEngine
) -> None:
    """A filing made today may not have moved to AttachHis yet."""
    url = transcript_url(1)
    historical = url.replace("stockinfo/AnnPdfOpen.aspx?Pname=", "xml-data/corpfiling/AttachHis/")
    live = historical.replace("/AttachHis/", "/AttachLive/")
    internet.pages[historical] = httpx.Response(404)
    internet.pages[live] = httpx.Response(200, content=pdf_for(live))
    await insert_fetch(admin_engine, url)

    await drain(context)

    assert internet.requests == [historical, live]
    assert await rows(admin_engine, "SELECT source_url, status FROM documents") == [
        (live, "completed")
    ]


async def test_a_filing_in_neither_folder_fails_without_retrying(
    context: WorkerContext, internet: FakeInternet, admin_engine: AsyncEngine
) -> None:
    url = transcript_url(1)
    historical = url.replace("stockinfo/AnnPdfOpen.aspx?Pname=", "xml-data/corpfiling/AttachHis/")
    internet.pages[historical] = httpx.Response(404)
    await insert_fetch(admin_engine, url)

    await run_once(context)

    [(status, error)] = await rows(admin_engine, "SELECT status, last_error FROM jobs")
    assert status == "failed"
    assert "not found" in error


async def test_downloads_are_spaced_out_to_be_gentle_with_bse(
    session_factory: Factory, tmp_path: Path, internet: FakeInternet, admin_engine: AsyncEngine
) -> None:
    import time

    await insert_fetch(admin_engine, transcript_url(1))
    async with httpx.AsyncClient(transport=httpx.MockTransport(internet)) as http:
        paced = WorkerContext(
            session_factory, FilesystemBlobStore(tmp_path), 300, http=http, fetch_pause_seconds=0.3
        )
        started = time.monotonic()
        await run_once(paced)
    assert time.monotonic() - started >= 0.3
