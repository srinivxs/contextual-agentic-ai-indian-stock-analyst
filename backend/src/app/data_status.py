"""How fresh the stored data is, and updating all of it in one click (the owner, 2026-09-29).

One "Update data" button replaces the per-stock "Check for new filings". A click queues, through
the very functions the worker's timers use, so every source keeps its own polite limit:

    filings   a discover_filings job per stock not checked within the hour (app/filings.py);
              it reads each stock's screener.in page once, so the fundamentals table and the
              official BSE filing links are refreshed together, and new filings are fetched
    prices    a sync_prices run (app/price_jobs.py) when some day is still missing; BSE's
              "slow down" cool-down still applies inside the job
    rbi       a poll_feed job (app/feed_jobs.py) at most once per RBI_REFRESH_MINUTES slot

New filings are then fingerprinted and read by the worker's own timers, when those are switched
on. And the button itself works once an hour (REFRESH_EVERY_MINUTES, the owner's rule, for
everyone together): each press is recorded in ``data_refreshes``, under a transaction-scoped
advisory lock so that two presses at the same moment cannot both pass the check, and the status
says when the next press is allowed. Nothing here fetches anything: the api only writes jobs,
and each answer says what happened per source ("queued", "recent": done within its limit,
"current": nothing missing, "off": its switch is off, so nothing the worker could not run is ever
queued).

The status is what every page shows as a disclaimer ("Data updated to ..."): when the filings
were last checked, the newest stored close, the newest live RBI release, and whether any update
job is still waiting or running.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import CommonSettings
from app.feed_jobs import enqueue_poll
from app.filings import CHECK_COOLDOWN_HOURS, enqueue_discovery
from app.price_jobs import enqueue_sync
from app.prices.store import days_to_fetch

RBI_REFRESH_MINUTES = 15  # the floor for polling a public site (FEED_POLL_MINUTES' minimum)
REFRESH_EVERY_MINUTES = 60  # "Update data" once an hour (the owner's rule)

# The jobs that bring data in; while one waits or runs, the data is still being updated.
_UPDATE_KINDS = (
    "discover_filings",
    "fetch_filing",
    "ingest_document",
    "embed_document",
    "extract_document",
    "sync_prices",
    "poll_feed",
)

_STATUS = text(
    """
    SELECT
      EXISTS (
        SELECT 1 FROM jobs
        WHERE status IN ('pending', 'processing') AND kind = ANY(:kinds)
      ) AS updating,
      (SELECT max(updated_at) FROM jobs
        WHERE kind = 'discover_filings' AND status = 'completed') AS filings_checked_at,
      (SELECT max(trade_date) FROM prices) AS prices_to,
      (SELECT max(published_at) FROM feed_items WHERE NOT is_fixture) AS rbi_to,
      (SELECT max(requested_at) + make_interval(mins => :every) FROM data_refreshes) AS next_at,
      now() AS now
    """
)
# One press at a time: a second transaction waits here until the first has committed its row.
_LOCK = text("SELECT pg_advisory_xact_lock(hashtext('data_refresh'))")
_NEXT = text(
    "SELECT max(requested_at) + make_interval(mins => :every) AS next_at, now() AS now "
    "FROM data_refreshes"
)
_RECORD = text("INSERT INTO data_refreshes (user_id) VALUES (:user_id)")


@dataclass(frozen=True)
class DataStatus:
    updating: bool  # an update job is waiting or running
    filings_checked_at: datetime | None  # the newest finished filings check
    prices_to: date | None  # the newest stored close
    rbi_to: datetime | None  # the newest live RBI press release stored
    next_update_at: datetime | None  # when "Update data" works again; None means now
    filings_on: bool  # FILINGS_DISCOVERY
    prices_on: bool  # PRICES_ENABLED
    rbi_live: bool  # FEED_MODE=live (else the RBI tab shows sample items)


async def data_status(db: AsyncSession, settings: CommonSettings) -> DataStatus:
    params = {"kinds": list(_UPDATE_KINDS), "every": REFRESH_EVERY_MINUTES}
    row = (await db.execute(_STATUS, params)).one()
    return DataStatus(
        updating=row.updating,
        filings_checked_at=row.filings_checked_at,
        prices_to=row.prices_to,
        rbi_to=row.rbi_to,
        next_update_at=row.next_at if row.next_at and row.next_at > row.now else None,
        filings_on=settings.filings_discovery,
        prices_on=settings.prices_enabled,
        rbi_live=settings.feed_mode == "live",
    )


FilingsOutcome = Literal["queued", "recent", "off"]
PricesOutcome = Literal["queued", "recent", "current", "off"]
RbiOutcome = Literal["queued", "recent"]


@dataclass(frozen=True)
class Refresh:
    filings: FilingsOutcome
    prices: PricesOutcome
    rbi: RbiOutcome


class TooSoon(Exception):
    """The last press was less than REFRESH_EVERY_MINUTES ago."""

    def __init__(self, next_at: datetime) -> None:
        super().__init__("Update data was pressed less than an hour ago")
        self.next_at = next_at


async def refresh(
    db: AsyncSession, settings: CommonSettings, *, today: date, user_id: UUID | None = None
) -> Refresh:
    """Record the press and queue every update its limits allow; the caller commits (which
    releases the lock). Raises TooSoon within the hour after the last press."""
    await db.execute(_LOCK)
    row = (await db.execute(_NEXT, {"every": REFRESH_EVERY_MINUTES})).one()
    if row.next_at is not None and row.next_at > row.now:
        raise TooSoon(row.next_at)
    await db.execute(_RECORD, {"user_id": user_id})

    filings: FilingsOutcome = "off"
    if settings.filings_discovery:
        queued = await enqueue_discovery(db, every_hours=CHECK_COOLDOWN_HOURS)
        filings = "queued" if queued else "recent"

    prices: PricesOutcome = "off"
    if settings.prices_enabled:
        history = settings.prices_history_days
        if not await days_to_fetch(db, today=today, history_days=history):
            prices = "current"
        else:
            queued_run = await enqueue_sync(
                db, every_minutes=settings.prices_run_minutes, history_days=history, today=today
            )
            prices = "queued" if queued_run else "recent"

    # In fixture mode the poll reads the shipped sample items: harmless, and the same path.
    rbi: RbiOutcome = (
        "queued" if await enqueue_poll(db, every_minutes=RBI_REFRESH_MINUTES) else "recent"
    )
    return Refresh(filings=filings, prices=prices, rbi=rbi)
