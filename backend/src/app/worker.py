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
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import filings
from app.blobs import BlobStore, FilesystemBlobStore
from app.core.config import get_worker_settings
from app.core.logging import configure_logging
from app.db.engine import create_db_engine, create_session_factory
from app.embedding_jobs import embed_document, enqueue_embeddings
from app.embeddings import BedrockEmbedder, Embedder, bedrock_client
from app.ingest import DocumentRejected, JobCannotSucceed, document_id_of, ingest_document
from app.ingest import mark_document as _mark_document
from app.jobs import ClaimedJob, claim_next, fail, retry_or_fail, still_mine

logger = logging.getLogger("app.worker")

GAVE_UP_REASON = "This document could not be processed after several attempts."


# How often the loop asks "is a filing discovery due?". The answer comes from the jobs table
# (enqueue_discovery), so this only bounds how late a due discovery can start.
DISCOVERY_CHECK_SECONDS = 600.0
# How often an idle-or-busy worker asks "does any ingested document still lack fingerprints?".
# One cheap query; a new filing is fingerprinted at most this long after it was read.
EMBED_CHECK_SECONDS = 60.0


@dataclass(frozen=True)
class WorkerContext:
    session_factory: async_sessionmaker[AsyncSession]
    blob_store: BlobStore
    lease_seconds: int
    # Only when FILINGS_DISCOVERY is on (ADR 018): the worker's one way onto the internet.
    http: httpx.AsyncClient | None = None
    filings_max_bytes: int = 60 * 1024 * 1024
    # A pause before every BSE download, to be gentle with it (tests set 0).
    fetch_pause_seconds: float = 2.0
    # How many years of filings to fetch (FILINGS_YEARS), and what "today" is (tests fix it).
    filings_years: int = 3
    today: Callable[[], date] = date.today
    # Only when EMBEDDINGS_ENABLED is on (P10): the one thing here that calls Bedrock.
    embedder: Embedder | None = None
    embedding_token_budget: int = 10_000_000
    embedding_concurrency: int = 4


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
        elif job.kind in ("discover_filings", "fetch_filing") and context.http is None:
            raise JobCannotSucceed("filing discovery is switched off (FILINGS_DISCOVERY)")
        elif job.kind == "discover_filings" and context.http is not None:
            await filings.discover(
                context.session_factory,
                context.http,
                job,
                today=context.today(),
                years=context.filings_years,
            )
        elif job.kind == "fetch_filing" and context.http is not None:
            await filings.fetch(
                context.session_factory,
                context.http,
                context.blob_store,
                job,
                limit=context.filings_max_bytes,
                pause_seconds=context.fetch_pause_seconds,
            )
        elif job.kind == "embed_document" and context.embedder is None:
            raise JobCannotSucceed("embeddings are switched off (EMBEDDINGS_ENABLED)")
        elif job.kind == "embed_document" and context.embedder is not None:
            await embed_document(
                context.session_factory,
                context.embedder,
                job,
                budget_tokens=context.embedding_token_budget,
                concurrency=context.embedding_concurrency,
            )
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


async def _queue_discovery(context: WorkerContext, every_hours: int) -> None:
    try:
        async with context.session_factory() as db:
            queued = await filings.enqueue_discovery(db, every_hours=every_hours)
            await db.commit()
        if queued:
            logger.info("filing_discovery_queued", extra={"stocks": queued})
    except Exception:
        logger.exception("filing_discovery_queue_error")


async def _queue_embeddings(context: WorkerContext, model: str) -> None:
    try:
        async with context.session_factory() as db:
            queued = await enqueue_embeddings(db, model=model)
            await db.commit()
        if queued:
            logger.info("embeddings_queued", extra={"documents": queued})
    except Exception:
        logger.exception("embeddings_queue_error")


async def run_forever(
    context: WorkerContext,
    stop: asyncio.Event,
    *,
    poll_seconds: float,
    discovery_every_hours: int | None = None,
    embed_model: str | None = None,
    embed_check_seconds: float = EMBED_CHECK_SECONDS,
) -> None:
    """Work through the queue until ``stop`` is set. The worker's small timers (ADR 008):

    - with ``discovery_every_hours``, queue a filing discovery per stock that often (ADR 018);
    - with ``embed_model``, every ``embed_check_seconds`` queue fingerprints for any ingested
      document still missing them under that model (P10).
    """
    next_discovery_check = 0.0
    next_embed_check = 0.0
    while True:
        if discovery_every_hours and time.monotonic() >= next_discovery_check:
            await _queue_discovery(context, discovery_every_hours)
            next_discovery_check = time.monotonic() + DISCOVERY_CHECK_SECONDS
        if embed_model and time.monotonic() >= next_embed_check:
            await _queue_embeddings(context, embed_model)
            next_embed_check = time.monotonic() + embed_check_seconds
        if stop.is_set():
            break
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
    # Created only when discovery is switched on, so a worker with it off cannot reach the internet.
    http = httpx.AsyncClient(timeout=30.0) if settings.filings_discovery else None
    # Likewise Bedrock: without the switch there is no client, and nothing can spend money.
    embedder = (
        BedrockEmbedder(model=settings.embedding_model, client=bedrock_client(settings.aws_region))
        if settings.embeddings_enabled
        else None
    )
    context = WorkerContext(
        session_factory=create_session_factory(engine),
        blob_store=FilesystemBlobStore(settings.blob_root),
        lease_seconds=settings.job_lease_seconds,
        http=http,
        filings_max_bytes=settings.filings_max_bytes,
        filings_years=settings.filings_years,
        embedder=embedder,
        embedding_token_budget=settings.embedding_token_budget,
        embedding_concurrency=settings.embedding_concurrency,
    )
    stop = asyncio.Event()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_number, lambda *_: stop.set())
    logger.info("worker_started")
    try:
        await run_forever(
            context,
            stop,
            poll_seconds=settings.worker_poll_seconds,
            discovery_every_hours=settings.filings_refresh_hours if http else None,
            embed_model=embedder.model if embedder else None,
        )
    finally:
        if http is not None:
            await http.aclose()
        await engine.dispose()
        logger.info("worker_stopped")


if __name__ == "__main__":  # pragma: no cover - the process entrypoint
    asyncio.run(_main())
