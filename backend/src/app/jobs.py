"""The job queue: one Postgres table (ADR 005), claimed with SKIP LOCKED, held by a lease.

A job moves pending -> processing -> completed, or back to pending to retry after a backoff, or to
failed. Plain SQL; none of these functions commits, the caller owns the transaction.

THE THREE RACES, AND WHAT CLOSES EACH
  1. Two workers poll at once.   The claim is ONE statement: UPDATE the row that a subquery picks
                                 with FOR UPDATE SKIP LOCKED. A row another worker is claiming is
                                 locked, so it is skipped, never waited for or taken twice.
  2. A worker dies mid-job.       The claim sets locked_until (the lease). A processing job whose
                                 lease has passed counts as claimable again.
  3. The "dead" worker was only   Every statement that finishes a job repeats the attempt number
     slow, and comes back.       the worker claimed with. A newer claim has incremented it, so the
                                 late worker's UPDATE matches no row and changes nothing (a fencing
                                 token).
"""

from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

ERROR_LIMIT = 500  # characters of an error kept on the job row; the full text is in the log

# THE LANES (the owner, 2026-10-07). The worker runs one WEB lane for the jobs that reach BSE and
# screener.in, one request at a time so it stays polite, and AI lanes for everything else, side by
# side. Every kind of job belongs to exactly one lane (tests/integration/test_worker_lanes_db.py).
WEB_KINDS: tuple[str, ...] = ("discover_filings", "fetch_filing", "sync_prices")
AI_KINDS: tuple[str, ...] = ("ingest_document", "embed_document", "extract_document", "poll_feed")


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int  # after this claim; also the fencing token
    max_attempts: int


_CLAIM = text(
    """
    UPDATE jobs SET
        status = 'processing',
        attempts = attempts + 1,
        locked_until = now() + make_interval(secs => :lease),
        updated_at = now()
    WHERE id = (
        SELECT id FROM jobs
        WHERE ((status = 'pending' AND run_after <= now())
               OR (status = 'processing' AND locked_until < now()))
          AND kind = ANY(CAST(:kinds AS text[]))
        ORDER BY run_after, id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id, kind, payload, attempts, max_attempts
    """
)

# Every finishing statement repeats the same fencing condition: this job, still processing, and
# still at the attempt number this worker claimed it with.
_COMPLETE = text(
    "UPDATE jobs SET status = 'completed', locked_until = NULL, last_error = NULL, "
    "updated_at = now() "
    "WHERE id = :id AND status = 'processing' AND attempts = :attempts"
)

_RETRY = text(
    "UPDATE jobs SET status = 'pending', locked_until = NULL, last_error = :error, "
    "run_after = now() + make_interval(secs => :delay), updated_at = now() "
    "WHERE id = :id AND status = 'processing' AND attempts = :attempts"
)

_FAIL = text(
    "UPDATE jobs SET status = 'failed', locked_until = NULL, last_error = :error, "
    "updated_at = now() "
    "WHERE id = :id AND status = 'processing' AND attempts = :attempts"
)

_LOCK_IF_MINE = text(
    "SELECT id FROM jobs "
    "WHERE id = :id AND status = 'processing' AND attempts = :attempts FOR UPDATE"
)


def backoff_seconds(attempts: int) -> int:
    """30 s after the first failure, doubling, never more than 15 minutes."""
    return int(min(30 * 2 ** (attempts - 1), 900))


def _fence(job: ClaimedJob) -> dict[str, int]:
    return {"id": job.id, "attempts": job.attempts}


async def _changed_one_row(db: AsyncSession, statement: Any, params: dict[str, Any]) -> bool:
    result = cast("CursorResult[Any]", await db.execute(statement, params))
    return result.rowcount == 1


async def claim_next(
    db: AsyncSession, *, lease_seconds: int, kinds: tuple[str, ...] = WEB_KINDS + AI_KINDS
) -> ClaimedJob | None:
    """The oldest runnable job among ``kinds`` (a lane's kinds; by default any), claimed."""
    row = (await db.execute(_CLAIM, {"lease": lease_seconds, "kinds": list(kinds)})).one_or_none()
    return None if row is None else ClaimedJob(**row._mapping)


async def still_mine(db: AsyncSession, job: ClaimedJob) -> bool:
    """Lock the job row if this worker still holds it. Call first in a transaction that writes
    results, so a newer claim cannot slip in between this check and the writes."""
    return (await db.execute(_LOCK_IF_MINE, _fence(job))).one_or_none() is not None


async def complete(db: AsyncSession, job: ClaimedJob) -> bool:
    """Mark the job done. False if another worker has taken it over meanwhile."""
    return await _changed_one_row(db, _COMPLETE, _fence(job))


async def fail(db: AsyncSession, job: ClaimedJob, error: str) -> bool:
    """Fail the job for good, with no retry: for work that can never succeed."""
    return await _changed_one_row(db, _FAIL, {**_fence(job), "error": error[:ERROR_LIMIT]})


async def retry_or_fail(
    db: AsyncSession, job: ClaimedJob, error: str
) -> Literal["pending", "failed", "lost"]:
    """After a failure that might pass: retry later, or fail if that was the last attempt."""
    if job.attempts >= job.max_attempts:
        return "failed" if await fail(db, job, error) else "lost"
    params = {**_fence(job), "error": error[:ERROR_LIMIT], "delay": backoff_seconds(job.attempts)}
    return "pending" if await _changed_one_row(db, _RETRY, params) else "lost"
