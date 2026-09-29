"""The sync_prices job and its timer, against the real database and a fake BSE (ADR 025).

Every company here is fictional (BSE codes 999901 and 999902) and every price is synthetic.
Nothing talks to bseindia.com, and nothing really waits: the pauses go to a recorder.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.blobs import FilesystemBlobStore
from app.jobs import ClaimedJob
from app.price_jobs import enqueue_sync, slot_key, sync_prices
from app.prices.model import bhavcopy_url
from app.worker import WorkerContext, run_forever, run_once
from tests.integration.price_helpers import DEMO_A, DEMO_B, demo_stocks

pytestmark = pytest.mark.usefixtures("migrated_db", "clean_document_tables", "demo_stocks")
__all__ = ["demo_stocks"]

Factory = async_sessionmaker[AsyncSession]
TODAY = date(2026, 9, 29)  # a Tuesday
HISTORY = 7  # candidates: Mon 28, Fri 25, Thu 24, Wed 23, Tue 22 (newest first)
HEADER = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
    "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,"
    "PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,"
    "TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4"
)


def bhavcopy(day: date) -> bytes:
    lines = [HEADER]
    for code in (DEMO_A, DEMO_B, "999903"):  # the third is a security we do not follow
        lines.append(
            f"{day},{day},CM,BSE,STK,{code},INE000X01011,DEMO,A,,,,,DEMO LTD.,10.00,11.00,9.00,"
            f"10.50,10.50,10.20,,10.40,,,1000,10000.00,10,F1,1,,,,,"
        )
    return ("\r\n".join(lines) + "\r\n").encode()


class FakeBse:
    """Serves a file for every date unless told otherwise; records the dates asked for."""

    def __init__(self) -> None:
        self.asked: list[date] = []
        self.status: dict[date, int] = {}
        self.urls: list[str] = []
        self.raise_on: date | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        stamp = str(request.url).rsplit("_", 3)[-3]
        day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
        self.asked.append(day)
        self.urls.append(str(request.url))
        if day == self.raise_on:
            raise httpx.ConnectError("no route to bseindia.com")
        status = self.status.get(day, 200)
        if status != 200:
            return httpx.Response(status, content=b"<html>Not Acceptable</html>")
        return httpx.Response(200, content=bhavcopy(day))


class Pauses:
    def __init__(self) -> None:
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


@pytest.fixture
def bse() -> FakeBse:
    return FakeBse()


@pytest.fixture
def pauses() -> Pauses:
    return Pauses()


@pytest.fixture
async def context(
    session_factory: Factory, tmp_path: Path, bse: FakeBse, pauses: Pauses
) -> AsyncIterator[WorkerContext]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(bse)) as http:
        yield make_context(session_factory, tmp_path, http, pauses)


def make_context(
    factory: Factory, tmp_path: Path, http: httpx.AsyncClient | None, pauses: Pauses, **more: Any
) -> WorkerContext:
    values: dict[str, Any] = {
        "prices_http": http,
        "prices_history_days": HISTORY,
        "prices_per_run": 12,
        "prices_pause_seconds": 20.0,
        "prices_cooldown_minutes": 20,
        "prices_sleep": pauses,
        "today": lambda: TODAY,
        "lease_seconds": 300,
        **more,
    }
    return WorkerContext(
        session_factory=factory,
        blob_store=FilesystemBlobStore(tmp_path / "blobs"),
        **values,
    )


async def rows(engine: AsyncEngine, sql: str) -> list[tuple[Any, ...]]:
    async with engine.connect() as connection:
        return [tuple(row) for row in await connection.execute(text(sql))]


async def run_job(
    context: WorkerContext, engine: AsyncEngine, key: str = "sync_prices:test"
) -> str:
    """Queue one sync job, let the worker run it, and return how the job ended."""
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('sync_prices', '{}', :k)"),
            {"k": key},
        )
    assert await run_once(context) is True
    [(status,)] = await rows(engine, f"SELECT status FROM jobs WHERE dedupe_key = '{key}'")  # noqa: S608
    return str(status)


PRICE_DAYS = "SELECT trade_date, status, attempts FROM price_days ORDER BY trade_date DESC"

ALL_DAYS = [
    date(2026, 9, 28),
    date(2026, 9, 25),
    date(2026, 9, 24),
    date(2026, 9, 23),
    date(2026, 9, 22),
]


# --- the run ------------------------------------------------------------------------------------


async def test_a_run_fetches_newest_first_stores_our_stocks_only_and_pauses_between_files(
    context: WorkerContext, bse: FakeBse, pauses: Pauses, admin_engine: AsyncEngine
) -> None:
    assert await run_job(context, admin_engine) == "completed"

    assert bse.asked == ALL_DAYS
    assert pauses.seconds == [20.0] * 4  # between files, not before the first or after the last
    assert await rows(admin_engine, "SELECT count(*) FROM prices") == [(10,)]  # 2 stocks x 5 days
    assert await rows(
        admin_engine,
        "SELECT DISTINCT s.bse_code FROM prices p JOIN stocks s ON s.id = p.stock_id ORDER BY 1",
    ) == [(DEMO_A,), (DEMO_B,)]  # the security we do not follow was left out
    assert [(d, s, a) for d, s, a in await rows(admin_engine, PRICE_DAYS)] == [
        (day, "fetched", 0) for day in ALL_DAYS
    ]


async def test_the_request_is_the_bse_file_address(
    context: WorkerContext, bse: FakeBse, admin_engine: AsyncEngine
) -> None:
    await run_job(context, admin_engine)
    assert bse.urls[0] == bhavcopy_url(date(2026, 9, 28))


async def test_a_run_stops_after_prices_per_run_files_and_the_next_run_continues(
    session_factory: Factory,
    tmp_path: Path,
    bse: FakeBse,
    pauses: Pauses,
    admin_engine: AsyncEngine,
) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(bse)) as http:
        context = make_context(session_factory, tmp_path, http, pauses, prices_per_run=2)
        await run_job(context, admin_engine, "sync_prices:one")
        assert bse.asked == ALL_DAYS[:2]
        assert pauses.seconds == [20.0]

        await run_job(context, admin_engine, "sync_prices:two")
        assert bse.asked == ALL_DAYS[:4]  # never asks again for a fetched day


async def test_a_run_with_nothing_to_do_asks_nobody(
    context: WorkerContext, bse: FakeBse, admin_engine: AsyncEngine
) -> None:
    await run_job(context, admin_engine, "sync_prices:one")
    bse.asked.clear()
    assert await run_job(context, admin_engine, "sync_prices:two") == "completed"
    assert bse.asked == []


async def test_the_first_slow_down_stops_the_run_and_costs_that_day_a_strike(
    context: WorkerContext, bse: FakeBse, pauses: Pauses, admin_engine: AsyncEngine
) -> None:
    bse.status[date(2026, 9, 25)] = 406

    assert await run_job(context, admin_engine) == "completed"  # slowing down is not a failure

    assert bse.asked == ALL_DAYS[:2]  # nothing after the refusal
    assert pauses.seconds == [20.0]
    assert await rows(admin_engine, PRICE_DAYS) == [
        (date(2026, 9, 28), "fetched", 0),
        (date(2026, 9, 25), "missing", 1),
    ]
    assert await rows(admin_engine, "SELECT count(*) FROM prices") == [(2,)]  # the 28th only


async def test_a_404_is_treated_like_a_406(
    context: WorkerContext, bse: FakeBse, admin_engine: AsyncEngine
) -> None:
    bse.status[date(2026, 9, 28)] = 404
    await run_job(context, admin_engine)
    assert bse.asked == [date(2026, 9, 28)]
    assert await rows(admin_engine, PRICE_DAYS) == [(date(2026, 9, 28), "missing", 1)]


async def age_refusals(engine: AsyncEngine, minutes: int = 21) -> None:
    """Let the cool-down pass: the refusals happened ``minutes`` ago."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE price_days SET updated_at = now() - make_interval(mins => :m) "
                "WHERE status <> 'fetched'"
            ),
            {"m": minutes},
        )


async def test_after_a_slow_down_no_run_asks_bse_until_the_cool_down_has_passed(
    context: WorkerContext,
    bse: FakeBse,
    admin_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Found on the first real run: a new time slot started a run 0.2 s after BSE's 406, which
    asked again at once, and three quick refusals turned a trading day into a "holiday"."""
    bse.status[date(2026, 9, 25)] = 406
    await run_job(context, admin_engine, "sync_prices:first")
    assert bse.asked == ALL_DAYS[:2]

    bse.asked.clear()
    with caplog.at_level(logging.INFO, logger="app.price_jobs"):
        assert await run_job(context, admin_engine, "sync_prices:too-soon") == "completed"
    assert bse.asked == []  # nobody was asked
    assert any(r.getMessage() == "prices_cooling_down" for r in caplog.records)
    assert await rows(admin_engine, PRICE_DAYS) == [
        (date(2026, 9, 28), "fetched", 0),
        (date(2026, 9, 25), "missing", 1),  # still one strike, not two
    ]

    await age_refusals(admin_engine, minutes=21)
    bse.status.clear()
    await run_job(context, admin_engine, "sync_prices:later")
    assert bse.asked[0] == date(2026, 9, 25)  # asked again only after the cool-down


async def test_three_strikes_make_a_holiday_and_the_run_moves_past_it(
    context: WorkerContext, bse: FakeBse, admin_engine: AsyncEngine
) -> None:
    bse.status[date(2026, 9, 28)] = 406  # the newest day never has a file
    for n in range(3):
        await run_job(context, admin_engine, f"sync_prices:{n}")
        await age_refusals(admin_engine)  # each strike comes after a full cool-down
    assert bse.asked == [date(2026, 9, 28)] * 3
    assert await rows(admin_engine, PRICE_DAYS) == [(date(2026, 9, 28), "no_file", 3)]

    bse.asked.clear()
    await run_job(context, admin_engine, "sync_prices:after")
    assert bse.asked == ALL_DAYS[1:]  # the holiday is not asked for again
    assert await rows(admin_engine, "SELECT count(*) FROM prices") == [(8,)]


async def test_an_html_page_with_status_200_counts_as_a_slow_down(
    session_factory: Factory, tmp_path: Path, pauses: Pauses, admin_engine: AsyncEngine
) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=b"<html>busy</html>"))
    async with httpx.AsyncClient(transport=transport) as http:
        await run_job(make_context(session_factory, tmp_path, http, pauses), admin_engine)
    assert await rows(admin_engine, PRICE_DAYS) == [(date(2026, 9, 28), "missing", 1)]
    assert await rows(admin_engine, "SELECT count(*) FROM prices") == [(0,)]


async def test_a_failed_fetch_retries_the_job_and_keeps_the_days_already_stored(
    context: WorkerContext, bse: FakeBse, admin_engine: AsyncEngine
) -> None:
    bse.raise_on = date(2026, 9, 25)
    assert await run_job(context, admin_engine) == "pending"  # will retry
    [(error,)] = await rows(admin_engine, "SELECT last_error FROM jobs")
    assert "ConnectError" in error
    assert await rows(admin_engine, PRICE_DAYS) == [(date(2026, 9, 28), "fetched", 0)]


async def test_a_server_error_is_a_failure_not_a_strike(
    session_factory: Factory, tmp_path: Path, pauses: Pauses, admin_engine: AsyncEngine
) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as http:
        broken = make_context(session_factory, tmp_path, http, pauses)
        assert await run_job(broken, admin_engine) == "pending"
    assert await rows(admin_engine, PRICE_DAYS) == []


async def test_a_run_gives_up_starting_files_when_the_lease_would_run_out(
    session_factory: Factory,
    tmp_path: Path,
    bse: FakeBse,
    pauses: Pauses,
    admin_engine: AsyncEngine,
) -> None:
    now = [0.0]

    async def slow_sleep(seconds: float) -> None:
        now[0] += seconds

    async with httpx.AsyncClient(transport=httpx.MockTransport(bse)) as http:
        context = make_context(
            session_factory,
            tmp_path,
            http,
            pauses,
            prices_sleep=slow_sleep,
            prices_pause_seconds=100.0,
            lease_seconds=300,
            prices_clock=lambda: now[0],
        )
        assert await run_job(context, admin_engine) == "completed"
    # lease 300 s, budget 80 % = 240 s: files at 0 s and 100 s and 200 s; a fourth would need 300 s
    assert bse.asked == ALL_DAYS[:3]


async def test_the_logs_carry_counts_and_never_prices(
    context: WorkerContext, admin_engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.price_jobs"):
        await run_job(context, admin_engine)
    [record] = [r for r in caplog.records if r.name == "app.price_jobs"]
    assert (record.days, record.rows, record.stopped) == (5, 10, False)  # type: ignore[attr-defined]
    assert "10.50" not in " ".join(str(vars(r)) + r.getMessage() for r in caplog.records)


# --- switched off, and a job that lost its lease ------------------------------------------------


async def test_prices_switched_off_can_never_succeed_and_says_why(
    session_factory: Factory, tmp_path: Path, pauses: Pauses, admin_engine: AsyncEngine
) -> None:
    context = make_context(session_factory, tmp_path, None, pauses)
    assert await run_job(context, admin_engine) == "failed"
    [(error,)] = await rows(admin_engine, "SELECT last_error FROM jobs")
    assert "PRICES_ENABLED" in error


async def test_a_run_that_lost_its_lease_writes_nothing(
    context: WorkerContext, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('sync_prices', '{}', 'k')")
        )
    stale = ClaimedJob(id=1, kind="sync_prices", payload={}, attempts=99, max_attempts=3)
    await sync_prices(
        session_factory,
        context.prices_http,
        stale,
        today=TODAY,
        history_days=HISTORY,
        per_run=12,
        pause_seconds=0,
    )
    assert await rows(admin_engine, "SELECT count(*) FROM prices") == [(0,)]
    assert await rows(admin_engine, PRICE_DAYS) == []


async def test_a_cooling_run_that_lost_its_lease_leaves_the_job_alone(
    context: WorkerContext, session_factory: Factory, bse: FakeBse, admin_engine: AsyncEngine
) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO price_days (trade_date, status, attempts) "
                "VALUES (DATE '2026-09-25', 'missing', 1)"
            )
        )
        await connection.execute(
            text("INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('sync_prices', '{}', 'k')")
        )
    stale = ClaimedJob(id=1, kind="sync_prices", payload={}, attempts=99, max_attempts=3)
    await sync_prices(
        session_factory,
        context.prices_http,
        stale,
        today=TODAY,
        history_days=HISTORY,
        per_run=12,
        pause_seconds=0,
        cooldown_minutes=20,
    )
    assert bse.asked == []
    assert await rows(admin_engine, "SELECT status FROM jobs WHERE dedupe_key = 'k'") == [
        ("pending",)
    ]


async def test_a_run_with_nothing_left_that_lost_its_lease_completes_nothing(
    context: WorkerContext, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await run_job(context, admin_engine, "sync_prices:first")  # every day is fetched now
    async with admin_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('sync_prices', '{}', 'k')")
        )
    stale = ClaimedJob(id=2, kind="sync_prices", payload={}, attempts=99, max_attempts=3)
    await sync_prices(
        session_factory,
        context.prices_http,
        stale,
        today=TODAY,
        history_days=HISTORY,
        per_run=12,
        pause_seconds=0,
    )
    assert await rows(admin_engine, "SELECT status FROM jobs WHERE dedupe_key = 'k'") == [
        ("pending",)
    ]


# --- the timer: several workers, one job per slot -----------------------------------------------

NOON = datetime(2026, 9, 29, 12, 20, tzinfo=UTC)


def test_the_slot_key_names_the_start_of_the_slot() -> None:
    assert slot_key(NOON, 5) == "sync_prices:2026-09-29T12:20:00Z"
    assert slot_key(datetime(2026, 9, 29, 12, 24, 59, tzinfo=UTC), 5) == slot_key(NOON, 5)
    assert slot_key(datetime(2026, 9, 29, 12, 25, 0, tzinfo=UTC), 5) == (
        "sync_prices:2026-09-29T12:25:00Z"
    )


async def enqueue(
    factory: Factory, *, now: datetime | None = NOON, today: date = TODAY, minutes: int = 5
) -> bool:
    async with factory() as db:
        queued = await enqueue_sync(
            db, every_minutes=minutes, history_days=HISTORY, today=today, now=now
        )
        await db.commit()
    return queued


async def sync_jobs(engine: AsyncEngine) -> list[tuple[Any, ...]]:
    return await rows(engine, "SELECT dedupe_key, status FROM jobs WHERE kind = 'sync_prices'")


async def test_the_second_enqueue_in_a_slot_queues_nothing(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    assert await enqueue(session_factory) is True
    assert await enqueue(session_factory) is False
    assert await sync_jobs(admin_engine) == [("sync_prices:2026-09-29T12:20:00Z", "pending")]


async def test_many_workers_asking_at_the_same_moment_queue_exactly_one_job(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    results = await asyncio.gather(*(enqueue(session_factory) for _ in range(8)))
    assert results.count(True) == 1
    assert len(await sync_jobs(admin_engine)) == 1


async def test_the_next_slot_gets_its_own_job(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    assert await enqueue(session_factory, now=NOON) is True
    assert await enqueue(session_factory, now=datetime(2026, 9, 29, 12, 25, 1, tzinfo=UTC)) is True
    assert len(await sync_jobs(admin_engine)) == 2


async def test_nothing_is_queued_when_every_day_is_already_done(
    context: WorkerContext, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await run_job(context, admin_engine, "sync_prices:first")
    assert await enqueue(session_factory) is False
    assert len(await sync_jobs(admin_engine)) == 1  # only the one we ran by hand


async def test_enqueue_uses_the_database_clock_when_not_given_one(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    assert await enqueue(session_factory, now=None, minutes=1440) is True
    assert await enqueue(session_factory, now=None, minutes=1440) is False
    [(key, _)] = await sync_jobs(admin_engine)
    assert key.startswith("sync_prices:")
    assert key.endswith("T00:00:00Z")


async def run_worker(context: WorkerContext, minutes: int, seconds: float) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_forever(
            context, stop, poll_seconds=0.01, prices_run_minutes=minutes, prices_check_seconds=0
        )
    )
    await asyncio.sleep(seconds)
    stop.set()
    await asyncio.wait_for(task, timeout=10)


async def test_two_running_workers_sync_the_prices_once_between_them(
    session_factory: Factory, tmp_path: Path, bse: FakeBse, admin_engine: AsyncEngine
) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(bse)) as http:
        first = make_context(session_factory, tmp_path, http, Pauses())
        second = make_context(session_factory, tmp_path, http, Pauses())
        # a one-day slot, so the two cannot straddle a slot boundary (but for UTC midnight)
        await asyncio.gather(run_worker(first, 1440, 1.5), run_worker(second, 1440, 1.5))

    jobs = await sync_jobs(admin_engine)
    assert len(jobs) == 1
    assert jobs[0][1] == "completed"
    assert sorted(bse.asked) == sorted(ALL_DAYS)  # each file was asked for once


async def test_a_timer_error_is_logged_and_the_worker_carries_on(
    tmp_path: Path, pauses: Pauses, caplog: pytest.LogCaptureFixture
) -> None:
    class Broken:
        def __call__(self) -> Callable[..., Any]:
            raise RuntimeError("database down")

    context = make_context(Broken(), tmp_path, None, pauses)  # type: ignore[arg-type]
    with caplog.at_level(logging.ERROR):
        await run_worker(context, 5, 0.2)
    assert any(r.getMessage() == "price_sync_queue_error" for r in caplog.records)
