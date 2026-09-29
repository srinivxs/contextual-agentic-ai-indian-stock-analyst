"""The scheduled RBI feed (P15): queueing a poll, and the ``poll_feed`` job itself.

WHY IS A TIMER INSIDE THE WORKER SAFE WITH SEVERAL WORKERS?
Every worker runs the same timer, so on a busy day three of them may all decide "a poll is due".
They cannot queue three polls, because "due" is not decided by a clock each keeps to itself but
by a row in the shared ``jobs`` table:

  * The job's dedupe key names the TIME SLOT, ``poll_feed:rbi:2026-09-29T10:00:00Z`` (the slot is
    the database clock rounded down to a whole number of ``FEED_POLL_MINUTES``). Every worker that
    asks in the same slot builds the same key.
  * The INSERT skips the slot if ANY job with that key exists, so a slot whose poll already
    finished is not polled again by a worker that only wakes up later.
  * Two workers can both pass that check at the same instant. The partial unique index on the
    live dedupe key (ADR 005) then lets exactly one insert through; ``ON CONFLICT DO NOTHING``
    makes the other a quiet no-op instead of an error.

So the guarantee is the database's, not the workers': one poll job per slot, however many workers
and however often each looks. And should a poll still run twice (a lease that expired mid-job), it
is harmless: the items are UNIQUE by URL and by title, so it stores nothing new.

THE POLL: no transaction spans the network. Read the saved validators (short transaction), do the
conditional GET (none open), then store items, events and the new validators in ONE short
transaction, so the ETag we remember always matches the items we kept.
"""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal, cast

import httpx
from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.feeds import store
from app.feeds.model import FEED_SOURCE
from app.feeds.rbi import fetch_feed, fixture_items, parse_feed
from app.feeds.tagging import ALIASES
from app.ingest import JobCannotSucceed
from app.jobs import ClaimedJob, complete, still_mine

logger = logging.getLogger("app.feed_jobs")

_ENQUEUE = text(
    """
    INSERT INTO jobs (kind, payload, dedupe_key)
    SELECT 'poll_feed', jsonb_build_object('source', CAST(:source AS text)), CAST(:key AS text)
    WHERE NOT EXISTS (SELECT 1 FROM jobs WHERE dedupe_key = :key)
    ON CONFLICT (dedupe_key) WHERE status IN ('pending', 'processing') DO NOTHING
    """
)


def slot_key(now: datetime, every_minutes: int) -> str:
    """The dedupe key of the time slot ``now`` falls in: the slot's start, in UTC."""
    seconds = every_minutes * 60
    start = datetime.fromtimestamp(now.timestamp() // seconds * seconds, UTC)
    return f"poll_feed:{FEED_SOURCE}:{start.strftime('%Y-%m-%dT%H:%M:%SZ')}"


async def enqueue_poll(
    db: AsyncSession, *, every_minutes: int, now: datetime | None = None
) -> bool:
    """Queue this slot's poll unless the slot already has one. True if this call queued it.

    ``now`` is the database's clock unless a test supplies one, so workers whose own clocks
    disagree still agree on the slot.
    """
    if now is None:
        now = (await db.execute(text("SELECT now()"))).scalar_one()
    result = cast(
        "CursorResult[Any]",
        await db.execute(_ENQUEUE, {"source": FEED_SOURCE, "key": slot_key(now, every_minutes)}),
    )
    return result.rowcount == 1


async def poll_feed(
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient | None,
    job: ClaimedJob,
    *,
    mode: Literal["live", "fixture"],
    aliases: Mapping[str, tuple[str, ...]] = ALIASES,
) -> None:
    """Run one poll_feed job. Raises on a failed fetch, so the job retries with backoff."""
    if mode == "fixture":
        # Offline by construction: nothing below touches ``http``, and nothing is remembered
        # about validators, because there is no server to send them back to.
        async with session_factory() as db:
            if not await still_mine(db, job):
                return
            stored = await store.add_items(db, fixture_items())
            events = await store.add_feed_events(db, stored, aliases)
            await complete(db, job)
            await db.commit()
        logger.info("feed_polled", extra={"mode": mode, "new_items": len(stored), "events": events})
        return

    if http is None:
        raise JobCannotSucceed("the live feed has no HTTP client (FEED_MODE=live)")

    async with session_factory() as db:
        state = await store.get_state(db)
    etag = state.etag if state else None
    last_modified = state.last_modified if state else None

    try:
        fetched = await fetch_feed(http, etag=etag, last_modified=last_modified)
    except Exception:
        async with session_factory() as db:
            if await still_mine(db, job):  # a stale worker leaves the state alone
                await store.save_state(
                    db, etag=etag, last_modified=last_modified, last_result="failed"
                )
                await db.commit()
        raise  # the worker records the error and schedules the retry

    async with session_factory() as db:
        if not await still_mine(db, job):
            return
        if fetched.status == "not_modified":
            new_items = events = 0
            await store.save_state(
                db, etag=etag, last_modified=last_modified, last_result="not_modified"
            )
        else:
            stored = await store.add_items(db, parse_feed(fetched.body))
            new_items = len(stored)
            events = await store.add_feed_events(db, stored, aliases)
            await store.save_state(
                db, etag=fetched.etag, last_modified=fetched.last_modified, last_result="ok"
            )
        await complete(db, job)
        await db.commit()
    logger.info(
        "feed_polled",
        extra={"mode": mode, "result": fetched.status, "new_items": new_items, "events": events},
    )
