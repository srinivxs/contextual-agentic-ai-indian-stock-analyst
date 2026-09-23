"""The Postgres job queue (ADR 005): claiming, leases, retries and backoff, in the real database.

Three races are closed here, each by the database rather than by application code:

1. Two workers polling at once never take the same job: the claim is one UPDATE whose subquery
   uses FOR UPDATE SKIP LOCKED, so a row one worker is claiming is invisible to the other.
2. A worker that dies mid-job does not strand it: the claim sets a lease (locked_until), and a
   job in processing whose lease has passed can be claimed again.
3. A worker that was presumed dead and comes back late cannot overwrite the new owner's result:
   every finishing statement repeats the attempt number it claimed with (a fencing token).
"""

import asyncio
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.jobs import ClaimedJob, backoff_seconds, claim_next, complete, retry_or_fail

pytestmark = pytest.mark.usefixtures("migrated_db", "clean_document_tables")

Factory = async_sessionmaker[AsyncSession]


async def enqueue(engine: AsyncEngine, key: str, **columns: Any) -> int:
    names = ", ".join(["kind", "payload", "dedupe_key", *columns])
    values = ", ".join(["'ingest_document'", "'{}'::jsonb", ":key", *(f":{c}" for c in columns)])
    async with engine.begin() as connection:
        job_id: int = (
            await connection.execute(
                text(f"INSERT INTO jobs ({names}) VALUES ({values}) RETURNING id"),  # noqa: S608 - column names come from this test file
                {"key": key, **columns},
            )
        ).scalar_one()
        return job_id


async def job(engine: AsyncEngine, job_id: int) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT status, attempts, last_error, locked_until IS NOT NULL AS leased, "
                    "EXTRACT(EPOCH FROM run_after - now()) AS wait FROM jobs WHERE id = :id"
                ),
                {"id": job_id},
            )
        ).one()
        return dict(row._mapping)


async def claim(factory: Factory, lease: int = 300) -> ClaimedJob | None:
    async with factory() as db:
        claimed = await claim_next(db, lease_seconds=lease)
        await db.commit()
        return claimed


async def test_a_pending_job_is_claimed_leased_and_counted(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    job_id = await enqueue(admin_engine, "ingest_document:1")

    claimed = await claim(session_factory)

    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.kind == "ingest_document"
    assert claimed.attempts == 1
    state = await job(admin_engine, job_id)
    assert state["status"] == "processing"
    assert state["leased"] is True


async def test_nothing_to_do_returns_none(session_factory: Factory) -> None:
    assert await claim(session_factory) is None


async def test_a_job_waiting_out_its_backoff_is_not_claimed_early(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, dedupe_key, run_after) "
                "VALUES ('ingest_document', 'ingest_document:2', now() + interval '1 hour')"
            )
        )
    assert await claim(session_factory) is None


async def test_the_oldest_runnable_job_is_claimed_first(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    first = await enqueue(admin_engine, "ingest_document:10")
    await enqueue(admin_engine, "ingest_document:11")

    claimed = await claim(session_factory)
    assert claimed is not None
    assert claimed.id == first


async def test_concurrent_workers_never_claim_the_same_job(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """Race 1. Eight workers, three jobs: three distinct claims, five empty-handed, no errors."""
    ids = {await enqueue(admin_engine, f"ingest_document:{n}") for n in range(3)}

    results = await asyncio.gather(*(claim(session_factory) for _ in range(8)))

    claimed = [r.id for r in results if r is not None]
    assert sorted(claimed) == sorted(ids)
    assert results.count(None) == 5


async def test_a_claim_never_waits_for_another_workers_claim(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """What SKIP LOCKED buys. Without it the second worker would still end up correct, but only
    after WAITING for the first worker's transaction; with it, it takes the next job at once."""
    first = await enqueue(admin_engine, "ingest_document:12")
    second = await enqueue(admin_engine, "ingest_document:13")

    async with session_factory() as holding:  # claims job 1 and keeps its row lock open
        held = await claim_next(holding, lease_seconds=300)
        assert held is not None
        assert held.id == first

        other = await asyncio.wait_for(claim(session_factory), timeout=3)

        assert other is not None
        assert other.id == second
        await holding.rollback()


async def test_a_job_whose_lease_expired_is_claimed_again(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """Race 2. The first worker died: after the lease, the job is not stranded."""
    job_id = await enqueue(admin_engine, "ingest_document:20")
    first = await claim(session_factory)
    assert first is not None
    assert await claim(session_factory) is None  # still leased: nobody else may take it

    async with admin_engine.begin() as connection:
        await connection.execute(
            text("UPDATE jobs SET locked_until = now() - interval '1 second' WHERE id = :id"),
            {"id": job_id},
        )
    second = await claim(session_factory)

    assert second is not None
    assert second.id == job_id
    assert second.attempts == 2


async def test_a_worker_that_lost_its_lease_cannot_finish_the_job(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """Race 3. The stale worker's `complete` must change nothing once someone else holds the job."""
    job_id = await enqueue(admin_engine, "ingest_document:21")
    stale = await claim(session_factory)
    assert stale is not None
    async with admin_engine.begin() as connection:
        await connection.execute(
            text("UPDATE jobs SET locked_until = now() - interval '1 second' WHERE id = :id"),
            {"id": job_id},
        )
    current = await claim(session_factory)
    assert current is not None

    async with session_factory() as db:
        finished = await complete(db, stale)
        await db.commit()

    assert finished is False
    assert (await job(admin_engine, job_id))["status"] == "processing"  # still the new owner's


async def test_completing_a_job_releases_it(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    job_id = await enqueue(admin_engine, "ingest_document:30")
    claimed = await claim(session_factory)
    assert claimed is not None

    async with session_factory() as db:
        assert await complete(db, claimed) is True
        await db.commit()

    state = await job(admin_engine, job_id)
    assert state["status"] == "completed"
    assert state["leased"] is False


async def test_a_failure_is_retried_later_with_backoff(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    job_id = await enqueue(admin_engine, "ingest_document:40")
    claimed = await claim(session_factory)
    assert claimed is not None

    async with session_factory() as db:
        outcome = await retry_or_fail(db, claimed, "TimeoutError: the store did not answer")
        await db.commit()

    assert outcome == "pending"
    state = await job(admin_engine, job_id)
    assert state["status"] == "pending"
    assert state["last_error"] == "TimeoutError: the store did not answer"
    assert state["leased"] is False
    assert 25 <= state["wait"] <= 30  # not runnable again for the first backoff step
    assert await claim(session_factory) is None


async def test_the_last_attempt_fails_for_good(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    job_id = await enqueue(admin_engine, "ingest_document:41", max_attempts=1)
    claimed = await claim(session_factory)
    assert claimed is not None

    async with session_factory() as db:
        outcome = await retry_or_fail(db, claimed, "boom")
        await db.commit()

    assert outcome == "failed"
    assert (await job(admin_engine, job_id))["status"] == "failed"


async def test_a_long_error_is_cut_short_before_it_is_stored(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    job_id = await enqueue(admin_engine, "ingest_document:42")
    claimed = await claim(session_factory)
    assert claimed is not None

    async with session_factory() as db:
        await retry_or_fail(db, claimed, "x" * 5000)
        await db.commit()

    assert len((await job(admin_engine, job_id))["last_error"]) == 500


def test_backoff_doubles_and_is_capped() -> None:
    assert [backoff_seconds(n) for n in (1, 2, 3, 4)] == [30, 60, 120, 240]
    assert backoff_seconds(20) == 900
