"""The worker: the second entrypoint of the one backend image (ADR 008). Run with

    python -m app.worker

It claims one job at a time from the Postgres queue (app/jobs.py), runs it, and records the
outcome. When there is nothing to do it waits ``worker_poll_seconds`` and looks again. SIGTERM or
Ctrl+C stop it after the job in hand, so a deploy never cuts a job off halfway; a job interrupted
anyway (a crash, a killed container) is claimed again once its lease runs out.

Why not FastAPI BackgroundTasks? They run inside the API process: a restart or a crash loses the
work, nothing retries it, and nothing stops two API tasks doing the same thing. A queue table gives
retries, leases and deduplication, and survives any restart.
"""

import asyncio
import contextlib
import logging
import signal
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.blobs import BlobStore, FilesystemBlobStore
from app.core.config import get_worker_settings
from app.core.logging import configure_logging
from app.db.engine import create_db_engine, create_session_factory
from app.ingest import DocumentRejected, JobCannotSucceed, document_id_of, ingest_document
from app.ingest import mark_document as _mark_document
from app.jobs import ClaimedJob, claim_next, fail, retry_or_fail, still_mine

logger = logging.getLogger("app.worker")

GAVE_UP_REASON = (
    "This document could not be processed after several attempts; try uploading it again later."
)


@dataclass(frozen=True)
class WorkerContext:
    session_factory: async_sessionmaker[AsyncSession]
    blob_store: BlobStore
    lease_seconds: int


async def mark_job_document(
    db: AsyncSession, job: ClaimedJob, status: str, reason: str | None
) -> None:
    """Show a job's outcome on its document. Only document jobs have one; others change nothing.

    Reached only after ingest_document accepted the payload, so document_id_of cannot fail here.
    """
    if job.kind == "ingest_document":
        await _mark_document(db, document_id_of(job), status, reason)


async def handle(context: WorkerContext, job: ClaimedJob) -> None:
    """Run one claimed job and record how it ended. Never raises for a job's own failure."""
    extra = {"job_id": job.id, "kind": job.kind, "attempt": job.attempts}
    logger.info("job_started", extra=extra)
    try:
        if job.kind == "ingest_document":
            await ingest_document(context.session_factory, context.blob_store, job)
        else:
            raise JobCannotSucceed(f"this worker has no handler for {job.kind!r} jobs yet")
    except DocumentRejected as rejected:
        async with context.session_factory() as db:
            if await still_mine(db, job):
                await mark_job_document(db, job, "failed", rejected.reason)
                await fail(db, job, rejected.reason)
                await db.commit()
        logger.info("job_rejected_document", extra=extra)
    except JobCannotSucceed as error:
        async with context.session_factory() as db:
            await fail(db, job, str(error))
            await db.commit()
        logger.warning("job_cannot_succeed", extra={**extra, "error": str(error)})
    except Exception as error:
        logger.exception("job_failed", extra=extra)
        async with context.session_factory() as db:
            outcome = await retry_or_fail(db, job, f"{type(error).__name__}: {error}")
            if outcome == "pending":
                await mark_job_document(db, job, "pending", None)
            elif outcome == "failed":
                await mark_job_document(db, job, "failed", GAVE_UP_REASON)
            await db.commit()
    else:
        logger.info("job_finished", extra=extra)


async def run_once(context: WorkerContext) -> bool:
    """Claim and run one job. False if there was nothing to do."""
    async with context.session_factory() as db:
        job = await claim_next(db, lease_seconds=context.lease_seconds)
        await db.commit()
    if job is None:
        return False
    await handle(context, job)
    return True


async def run_forever(context: WorkerContext, stop: asyncio.Event, *, poll_seconds: float) -> None:
    while not stop.is_set():
        try:
            worked = await run_once(context)
        except Exception:
            # The database is down, say. Log it, wait, and try again rather than exiting.
            logger.exception("worker_loop_error")
            worked = False
        if not worked:
            with contextlib.suppress(TimeoutError):  # the normal case: nothing arrived to stop us
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)


async def _main() -> None:  # pragma: no cover - process wiring; the container tests run it (P9c)
    settings = get_worker_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings)
    context = WorkerContext(
        session_factory=create_session_factory(engine),
        blob_store=FilesystemBlobStore(settings.blob_root),
        lease_seconds=settings.job_lease_seconds,
    )
    stop = asyncio.Event()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_number, lambda *_: stop.set())
    logger.info("worker_started")
    try:
        await run_forever(context, stop, poll_seconds=settings.worker_poll_seconds)
    finally:
        await engine.dispose()
        logger.info("worker_stopped")


if __name__ == "__main__":  # pragma: no cover - the process entrypoint
    asyncio.run(_main())
