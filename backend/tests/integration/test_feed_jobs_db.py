"""The poll_feed job and its timer, against the real database and a fake internet (P15).

Every company and release here is synthetic (DemoCo); "TCS" appears only as the followed stock a
synthetic alias is pointed at. Nothing talks to rbi.org.in.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.blobs import FilesystemBlobStore
from app.feed_jobs import enqueue_poll, poll_feed, slot_key
from app.feeds.model import RBI_FEED_URL
from app.feeds.rbi import fixture_items
from app.jobs import ClaimedJob
from app.worker import WorkerContext, run_forever, run_once

pytestmark = pytest.mark.usefixtures("migrated_db", "clean_document_tables")

Factory = async_sessionmaker[AsyncSession]
ALIASES = {"TCS": ("DemoCo",)}


@pytest.fixture(autouse=True)
async def empty_feed(admin_engine: AsyncEngine) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(text("TRUNCATE feed_items, feed_state CASCADE"))


def rss(*items: tuple[str, int]) -> bytes:
    body = "".join(
        f"<item><title>{title}</title><link>https://www.rbi.org.in/demo/{n}</link>"
        f"<description>Synthetic text {n}.</description>"
        f"<pubDate>Tue, 01 Sep 2026 10:00:00 GMT</pubDate></item>"
        for title, n in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{body}</channel></rss>'.encode()


class FakeRbi:
    """A fake RBI: serves ``body`` with an ETag, answers 304 to a matching If-None-Match."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.etag = '"v1"'
        self.requests: list[httpx.Request] = []
        self.fail: Exception | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail is not None:
            raise self.fail
        if request.headers.get("If-None-Match") == self.etag:
            return httpx.Response(304)
        return httpx.Response(200, content=self.body, headers={"ETag": self.etag})


@pytest.fixture
def rbi() -> FakeRbi:
    return FakeRbi(rss(("DemoCo appointment of a director", 1), ("Weekly supplement", 2)))


@pytest.fixture
async def live(
    session_factory: Factory, tmp_path: Path, rbi: FakeRbi
) -> AsyncIterator[WorkerContext]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(rbi)) as http:
        yield WorkerContext(
            session_factory=session_factory,
            blob_store=FilesystemBlobStore(tmp_path / "blobs"),
            lease_seconds=300,
            feed_mode="live",
            feed_http=http,
            feed_aliases=ALIASES,
        )


def offline_context(session_factory: Factory, tmp_path: Path, **overrides: Any) -> WorkerContext:
    return WorkerContext(
        session_factory=session_factory,
        blob_store=FilesystemBlobStore(tmp_path / "blobs"),
        lease_seconds=300,
        **overrides,
    )


async def rows(engine: AsyncEngine, sql: str) -> list[tuple[Any, ...]]:
    async with engine.connect() as connection:
        return [tuple(row) for row in await connection.execute(text(sql))]


async def queue_job(engine: AsyncEngine, key: str = "poll_feed:rbi:test") -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, payload, dedupe_key) "
                "VALUES ('poll_feed', '{\"source\": \"rbi\"}', :key)"
            ),
            {"key": key},
        )


async def poll(context: WorkerContext, engine: AsyncEngine, key: str = "poll_feed:rbi:test") -> str:
    """Queue one poll job, let the worker run it, and return how the job ended."""
    await queue_job(engine, key)
    assert await run_once(context) is True
    [(status,)] = await rows(engine, f"SELECT status FROM jobs WHERE dedupe_key = '{key}'")  # noqa: S608
    return str(status)


# --- the slot key (pure) ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "minutes", "expected"),
    [
        (datetime(2026, 9, 29, 10, 7, 33, tzinfo=UTC), 60, "poll_feed:rbi:2026-09-29T10:00:00Z"),
        (datetime(2026, 9, 29, 10, 59, 59, tzinfo=UTC), 60, "poll_feed:rbi:2026-09-29T10:00:00Z"),
        (datetime(2026, 9, 29, 11, 0, 0, tzinfo=UTC), 60, "poll_feed:rbi:2026-09-29T11:00:00Z"),
        (datetime(2026, 9, 29, 10, 44, 0, tzinfo=UTC), 15, "poll_feed:rbi:2026-09-29T10:30:00Z"),
        (datetime(2026, 9, 29, 23, 59, 0, tzinfo=UTC), 1440, "poll_feed:rbi:2026-09-29T00:00:00Z"),
    ],
)
def test_the_slot_key_names_the_start_of_the_slot(
    now: datetime, minutes: int, expected: str
) -> None:
    assert slot_key(now, minutes) == expected


# --- fixture mode: offline --------------------------------------------------------------------


def no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"fixture mode must not touch the network: {request.url}")


async def test_fixture_mode_stores_the_synthetic_items_without_any_network(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(no_network)) as http:
        context = offline_context(session_factory, tmp_path, feed_mode="fixture", feed_http=http)
        assert await poll(context, admin_engine) == "completed"
    stored = await rows(admin_engine, "SELECT title, is_fixture FROM feed_items")
    assert len(stored) == len(fixture_items()) > 0
    assert all(is_fixture for _, is_fixture in stored)
    assert await rows(admin_engine, "SELECT * FROM feed_state") == []  # no server, no validators
    assert await rows(admin_engine, "SELECT * FROM events") == []  # they name no followed stock


async def test_fixture_mode_needs_no_client_and_repeating_it_adds_nothing(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    context = offline_context(session_factory, tmp_path)  # the defaults: fixture, no client
    assert await poll(context, admin_engine, "poll_feed:rbi:one") == "completed"
    assert await poll(context, admin_engine, "poll_feed:rbi:two") == "completed"
    assert len(await rows(admin_engine, "SELECT id FROM feed_items")) == len(fixture_items())


# --- live mode --------------------------------------------------------------------------------


async def test_a_live_poll_stores_items_and_events_and_remembers_the_etag(
    live: WorkerContext, rbi: FakeRbi, admin_engine: AsyncEngine
) -> None:
    assert await poll(live, admin_engine) == "completed"

    assert rbi.requests[0].url == RBI_FEED_URL
    assert "If-None-Match" not in rbi.requests[0].headers  # nothing remembered yet
    assert await rows(admin_engine, "SELECT title, is_fixture FROM feed_items ORDER BY id") == [
        ("DemoCo appointment of a director", False),
        ("Weekly supplement", False),
    ]
    assert await rows(
        admin_engine,
        "SELECT s.symbol, e.event_type, e.feed_item_id IS NOT NULL FROM events e "
        "JOIN stocks s ON s.id = e.stock_id",
    ) == [("TCS", "management_change", True)]
    assert await rows(
        admin_engine, "SELECT etag, last_result, last_polled_at IS NOT NULL FROM feed_state"
    ) == [('"v1"', "ok", True)]


async def test_the_next_poll_sends_the_etag_and_a_304_changes_nothing(
    live: WorkerContext, rbi: FakeRbi, admin_engine: AsyncEngine
) -> None:
    await poll(live, admin_engine, "poll_feed:rbi:one")
    before = await rows(admin_engine, "SELECT id, title FROM feed_items ORDER BY id")

    assert await poll(live, admin_engine, "poll_feed:rbi:two") == "completed"

    assert rbi.requests[1].headers["If-None-Match"] == '"v1"'
    assert await rows(admin_engine, "SELECT id, title FROM feed_items ORDER BY id") == before
    assert len(await rows(admin_engine, "SELECT id FROM events")) == 1
    assert await rows(admin_engine, "SELECT etag, last_result FROM feed_state") == [
        ('"v1"', "not_modified")
    ]


async def test_a_changed_feed_stores_only_what_is_new(
    live: WorkerContext, rbi: FakeRbi, admin_engine: AsyncEngine
) -> None:
    await poll(live, admin_engine, "poll_feed:rbi:one")
    rbi.body = rss(
        ("DemoCo appointment of a director", 1),
        ("Weekly supplement", 2),
        ("Monetary penalty on DemoCo", 3),
    )
    rbi.etag = '"v2"'

    assert await poll(live, admin_engine, "poll_feed:rbi:two") == "completed"

    assert [t for (t,) in await rows(admin_engine, "SELECT title FROM feed_items ORDER BY id")] == [
        "DemoCo appointment of a director",
        "Weekly supplement",
        "Monetary penalty on DemoCo",
    ]
    assert len(await rows(admin_engine, "SELECT id FROM events")) == 2
    assert await rows(admin_engine, "SELECT etag FROM feed_state") == [('"v2"',)]


async def test_a_failed_fetch_is_recorded_kept_for_retry_and_keeps_the_old_etag(
    live: WorkerContext, rbi: FakeRbi, admin_engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    await poll(live, admin_engine, "poll_feed:rbi:one")
    rbi.fail = httpx.ConnectError("no route to rbi.org.in")

    with caplog.at_level(logging.INFO):
        assert await poll(live, admin_engine, "poll_feed:rbi:two") == "pending"  # will retry

    assert await rows(admin_engine, "SELECT etag, last_result FROM feed_state") == [
        ('"v1"', "failed")
    ]
    [(error,)] = await rows(
        admin_engine, "SELECT last_error FROM jobs WHERE dedupe_key = 'poll_feed:rbi:two'"
    )
    assert "ConnectError" in error
    assert len(await rows(admin_engine, "SELECT id FROM feed_items")) == 2  # nothing lost or added


async def test_a_first_ever_failure_creates_the_state_row(
    live: WorkerContext, rbi: FakeRbi, admin_engine: AsyncEngine
) -> None:
    rbi.fail = httpx.ConnectError("down")
    assert await poll(live, admin_engine) == "pending"
    assert await rows(admin_engine, "SELECT etag, last_result FROM feed_state") == [
        (None, "failed")
    ]


async def test_live_mode_without_a_client_can_never_succeed(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    context = offline_context(session_factory, tmp_path, feed_mode="live")
    assert await poll(context, admin_engine) == "failed"
    [(error,)] = await rows(admin_engine, "SELECT last_error FROM jobs")
    assert "FEED_MODE=live" in error


async def test_the_logs_carry_counts_and_never_item_text(
    live: WorkerContext, admin_engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.feed_jobs"):
        await poll(live, admin_engine)
    [record] = [r for r in caplog.records if r.name == "app.feed_jobs"]
    assert (record.new_items, record.events) == (2, 1)  # type: ignore[attr-defined]
    everything = " ".join(str(vars(r)) + r.getMessage() for r in caplog.records)
    assert "DemoCo" not in everything
    assert "appointment" not in everything


# --- a poll whose job was taken over writes nothing -------------------------------------------


@pytest.mark.parametrize("mode", ["fixture", "live"])
async def test_a_poll_that_lost_its_lease_stores_nothing(
    mode: str, live: WorkerContext, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await queue_job(admin_engine)
    stale = ClaimedJob(id=1, kind="poll_feed", payload={}, attempts=99, max_attempts=3)
    await poll_feed(
        session_factory,
        live.feed_http,
        stale,
        mode="live" if mode == "live" else "fixture",
        aliases=ALIASES,
    )
    assert await rows(admin_engine, "SELECT id FROM feed_items") == []
    assert await rows(admin_engine, "SELECT source FROM feed_state") == []


async def test_a_failed_poll_that_lost_its_lease_leaves_the_state_alone(
    live: WorkerContext, rbi: FakeRbi, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """The security review's low finding: a stale worker's failure must not overwrite the state
    the worker now holding the job keeps."""
    await queue_job(admin_engine)
    rbi.fail = httpx.ConnectError("down")
    stale = ClaimedJob(id=1, kind="poll_feed", payload={}, attempts=99, max_attempts=3)
    with pytest.raises(httpx.ConnectError):
        await poll_feed(session_factory, live.feed_http, stale, mode="live", aliases=ALIASES)
    assert await rows(admin_engine, "SELECT source FROM feed_state") == []


# --- the timer: several workers, one job per slot ---------------------------------------------

NOON = datetime(2026, 9, 29, 12, 20, tzinfo=UTC)


async def enqueue(factory: Factory, *, minutes: int = 60, now: datetime | None = NOON) -> bool:
    async with factory() as db:
        queued = await enqueue_poll(db, every_minutes=minutes, now=now)
        await db.commit()
    return queued


async def poll_jobs(engine: AsyncEngine) -> list[tuple[Any, ...]]:
    return await rows(engine, "SELECT dedupe_key, status FROM jobs WHERE kind = 'poll_feed'")


async def test_the_second_enqueue_in_a_slot_queues_nothing(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    assert await enqueue(session_factory) is True
    assert await enqueue(session_factory) is False
    assert await poll_jobs(admin_engine) == [("poll_feed:rbi:2026-09-29T12:00:00Z", "pending")]


async def test_many_workers_asking_at_the_same_moment_queue_exactly_one_job(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """The interview question: why is a timer inside the worker safe with several workers?
    Because the slot's dedupe key plus the jobs table's unique index decide, not the workers."""
    results = await asyncio.gather(*(enqueue(session_factory) for _ in range(8)))
    assert results.count(True) == 1
    assert len(await poll_jobs(admin_engine)) == 1


async def test_a_worker_waking_after_the_poll_finished_does_not_queue_it_again(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    await enqueue(session_factory)
    assert await run_once(offline_context(session_factory, tmp_path)) is True  # the poll runs
    assert await poll_jobs(admin_engine) == [("poll_feed:rbi:2026-09-29T12:00:00Z", "completed")]

    assert await enqueue(session_factory) is False  # a late worker, same slot

    assert len(await poll_jobs(admin_engine)) == 1


async def test_the_next_slot_gets_its_own_job(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    assert await enqueue(session_factory, now=NOON) is True
    assert await enqueue(session_factory, now=datetime(2026, 9, 29, 13, 0, 1, tzinfo=UTC)) is True
    assert len(await poll_jobs(admin_engine)) == 2


async def test_enqueue_uses_the_database_clock_when_not_given_one(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    assert await enqueue(session_factory, minutes=1440, now=None) is True
    assert await enqueue(session_factory, minutes=1440, now=None) is False
    [(key, _)] = await poll_jobs(admin_engine)
    assert key.startswith("poll_feed:rbi:")
    assert key.endswith("T00:00:00Z")


async def run_worker(context: WorkerContext, minutes: int, seconds: float) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_forever(
            context, stop, poll_seconds=0.01, feed_poll_minutes=minutes, feed_check_seconds=0
        )
    )
    await asyncio.sleep(seconds)
    stop.set()
    await asyncio.wait_for(task, timeout=5)


async def test_two_running_workers_poll_the_feed_once_between_them(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    # A one-day slot, so the two workers cannot straddle a slot boundary (but for UTC midnight).
    first = offline_context(session_factory, tmp_path)
    second = offline_context(session_factory, tmp_path)
    await asyncio.gather(run_worker(first, 1440, 1.0), run_worker(second, 1440, 1.0))

    jobs = await poll_jobs(admin_engine)
    assert len(jobs) == 1
    assert jobs[0][1] == "completed"
    assert len(await rows(admin_engine, "SELECT id FROM feed_items")) == len(fixture_items())


async def test_a_timer_error_is_logged_and_the_worker_carries_on(
    admin_engine: AsyncEngine, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    class Broken:
        def __call__(self) -> Callable[..., Any]:
            raise RuntimeError("database down")

    context = offline_context(Broken(), tmp_path)  # type: ignore[arg-type]
    with caplog.at_level(logging.ERROR):
        await run_worker(context, 60, 0.2)
    assert any(r.getMessage() == "feed_poll_queue_error" for r in caplog.records)
