"""Share prices (ADR 025): queueing a run, and the ``sync_prices`` job itself.

WHERE THE PRICES COME FROM. BSE publishes one file per trading day (app/prices/bse.py). A full
year is about 250 files, and BSE will not hand them over quickly.

THE 406 RULE. Ask for files a few seconds apart and BSE answers HTTP 406 with a small HTML page;
the same file downloads fine after a pause of about half a minute. A weekend or a market holiday
has no file and answers 404 or 406 too. From outside, "too fast" and "no file" look the same. So:

  * PACING. A run fetches at most ``PRICES_PER_RUN`` files, newest first, with
    ``PRICES_PAUSE_SECONDS`` between them (and never longer than 80 % of the job's lease, so a
    slow run cannot outlive its lease and be taken over halfway).
  * ONE REFUSAL ENDS THE RUN. The first "slow down" means BSE is asking us to stop: the day gets
    a strike (``price_days.attempts``) and the run finishes. It is not an error and nothing is
    retried at once; the next run, minutes later, continues.
  * A COOL-DOWN. After any refusal, no run asks BSE for ``PRICES_COOLDOWN_MINUTES`` (20). The
    first real run showed why: a new time slot started a run 0.2 s after a 406, which asked
    again at once, and three quick refusals turned a normal Friday into a "holiday".
  * THREE STRIKES = A HOLIDAY. A day that has answered "slow down" three times, each after a
    full cool-down, is marked ``no_file`` and never asked for again. (A day that was only
    rate-limited three times that far apart is lost the same way; the trade is accepted, a
    missing day in a price history is harmless.)
  * Days already fetched are never asked for again, so a year fills in overnight (about 8 to
    12 hours at 5 files a run) and then the timer has one file a day to fetch.

WHY IS A TIMER INSIDE THE WORKER SAFE WITH SEVERAL WORKERS? The same way as the feed poll
(app/feed_jobs.py): the job's dedupe key names the time slot, ``sync_prices:2026-09-29T10:05:00Z``,
so every worker asking in the slot builds the same key and the jobs table lets exactly one
through. A run that runs twice anyway is harmless: ``prices`` is UNIQUE by (stock, day).

THE RUN: no transaction spans the network. Read the stock codes and the days still to fetch
(short transaction), fetch a file (none open), then store its rows and mark the day in ONE short
transaction, after checking the job is still ours (a stale worker writes nothing).
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any, cast

import httpx
from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingest import JobCannotSucceed
from app.jobs import ClaimedJob, complete, still_mine
from app.prices import store
from app.prices.bse import fetch_day

logger = logging.getLogger("app.price_jobs")

LEASE_SHARE = 0.8  # a run never plans to use more than this share of its lease

_ENQUEUE = text(
    """
    INSERT INTO jobs (kind, payload, dedupe_key)
    SELECT 'sync_prices', '{}'::jsonb, CAST(:key AS text)
    WHERE NOT EXISTS (SELECT 1 FROM jobs WHERE dedupe_key = :key)
    ON CONFLICT (dedupe_key) WHERE status IN ('pending', 'processing') DO NOTHING
    """
)


def slot_key(now: datetime, every_minutes: int) -> str:
    """The dedupe key of the time slot ``now`` falls in: the slot's start, in UTC."""
    seconds = every_minutes * 60
    start = datetime.fromtimestamp(now.timestamp() // seconds * seconds, UTC)
    return f"sync_prices:{start.strftime('%Y-%m-%dT%H:%M:%SZ')}"


async def enqueue_sync(
    db: AsyncSession,
    *,
    every_minutes: int,
    history_days: int,
    today: date,
    now: datetime | None = None,
) -> bool:
    """Queue this slot's run unless the slot already has one, or every day is already done.
    True if this call queued it. ``now`` is the database's clock unless a test supplies one."""
    if not await store.days_to_fetch(db, today=today, history_days=history_days):
        return False
    if now is None:
        now = (await db.execute(text("SELECT now()"))).scalar_one()
    result = cast(
        "CursorResult[Any]", await db.execute(_ENQUEUE, {"key": slot_key(now, every_minutes)})
    )
    return result.rowcount == 1


async def sync_prices(
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient | None,
    job: ClaimedJob,
    *,
    today: date,
    history_days: int,
    per_run: int,
    pause_seconds: float,
    cooldown_minutes: int = 20,
    max_seconds: float | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Run one sync_prices job. A failed fetch raises, so the job retries with backoff (the days
    already stored stay stored). Tests inject ``sleep`` and ``clock`` so nothing really waits."""
    if http is None:
        raise JobCannotSucceed("share prices are switched off (PRICES_ENABLED)")

    started = clock()
    async with session_factory() as db:
        codes = {str(code) for (code,) in await db.execute(text("SELECT bse_code FROM stocks"))}
        days = await store.days_to_fetch(db, today=today, history_days=history_days)
        cooling = await store.cooling_down(db, minutes=cooldown_minutes)
    if cooling:  # BSE said "slow down" not long ago: ask nobody until the cool-down has passed
        async with session_factory() as db:
            if await still_mine(db, job):
                await complete(db, job)
                await db.commit()
        logger.info("prices_cooling_down", extra={"left": len(days)})
        return

    fetched_days = stored_rows = 0
    stopped = False
    for position, day in enumerate(days[:per_run]):
        if position:
            if max_seconds is not None and clock() - started + pause_seconds > max_seconds:
                break  # not enough lease left for another pause and file
            await sleep(pause_seconds)
        outcome, prices = await fetch_day(http, day, codes=codes)
        async with session_factory() as db:
            if not await still_mine(db, job):
                return
            if outcome == "fetched":
                stored_rows += await store.add_prices(db, prices)
                await store.mark_day(db, day, "fetched")
                fetched_days += 1
            else:
                await store.mark_day(db, day, "missing")
                stopped = True
            await db.commit()
        if stopped:
            break  # BSE asked us to slow down: the next run continues

    async with session_factory() as db:
        if not await still_mine(db, job):
            return
        await complete(db, job)
        await db.commit()
    logger.info(
        "prices_synced",
        extra={"days": fetched_days, "rows": stored_rows, "stopped": stopped, "left": len(days)},
    )
