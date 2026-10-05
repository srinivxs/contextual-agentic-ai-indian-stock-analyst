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
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import filings
from app.blobs import BlobStore, make_blob_store
from app.clock import india_today
from app.core.config import get_worker_settings
from app.core.logging import configure_logging
from app.db.engine import create_db_engine, create_session_factory
from app.embedding_jobs import embed_document, enqueue_embeddings
from app.embeddings import BedrockEmbedder, Embedder, bedrock_client
from app.extraction_jobs import EXTRACTOR_VERSION, enqueue_extractions, extract_document
from app.feed_jobs import enqueue_poll, poll_feed
from app.feeds.tagging import ALIASES
from app.ingest import DocumentRejected, JobCannotSucceed, document_id_of, ingest_document
from app.ingest import mark_document as _mark_document
from app.jobs import ClaimedJob, claim_next, fail, retry_or_fail, still_mine
from app.llm import BedrockLlm, StructuredLlm, bedrock_llm_client
from app.price_jobs import LEASE_SHARE, enqueue_sync, sync_prices

logger = logging.getLogger("app.worker")

GAVE_UP_REASON = "This document could not be processed after several attempts."


# How often the loop asks "is a filing discovery due?". The answer comes from the jobs table
# (enqueue_discovery), so this only bounds how late a due discovery can start.
DISCOVERY_CHECK_SECONDS = 600.0
# How often an idle-or-busy worker asks "does any ingested document still lack fingerprints?".
# One cheap query; a new filing is fingerprinted at most this long after it was read.
EMBED_CHECK_SECONDS = 60.0
# The same for facts and events: a new filing is read at most this long after it was ingested.
EXTRACT_CHECK_SECONDS = 60.0
# How often the loop asks "has this time slot's feed poll been queued?" One cheap query; the slot's
# dedupe key (app/feed_jobs.py) is what keeps several workers from queueing it twice.
FEED_CHECK_SECONDS = 60.0
# The same for the price sync (ADR 025): is this slot's run queued, and is anything left to fetch?
PRICES_CHECK_SECONDS = 60.0


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
    today: Callable[[], date] = india_today
    # Only when EMBEDDINGS_ENABLED is on (P10): the one thing here that calls Bedrock.
    embedder: Embedder | None = None
    embedding_token_budget: int = 10_000_000
    embedding_concurrency: int = 4
    # Only when EXTRACTION_ENABLED is on (P11): the LLM that reads filings for facts and events.
    llm: StructuredLlm | None = None
    extraction_version: str = EXTRACTOR_VERSION
    extraction_budget_usd: Decimal = Decimal("2.00")
    llm_input_usd_per_mtok: Decimal = Decimal("0.35")
    llm_output_usd_per_mtok: Decimal = Decimal("2.95")
    extraction_concurrency: int = 2
    # The RBI feed (P15). "fixture" reads the synthetic items and never needs a client; "live"
    # needs ``feed_http``, a client made only in that mode, so an offline worker cannot reach RBI.
    feed_mode: Literal["live", "fixture"] = "fixture"
    feed_http: httpx.AsyncClient | None = None
    feed_aliases: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: dict(ALIASES))
    # Share prices (ADR 025): a client for BSE's daily files exists only with PRICES_ENABLED. The
    # pause and clock are injectable so tests never wait.
    prices_http: httpx.AsyncClient | None = None
    prices_history_days: int = 30
    prices_per_run: int = 5
    prices_pause_seconds: float = 30.0
    prices_cooldown_minutes: int = 20
    prices_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    prices_clock: Callable[[], float] = time.monotonic


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
        elif job.kind == "poll_feed":
            await poll_feed(
                context.session_factory,
                context.feed_http,
                job,
                mode=context.feed_mode,
                aliases=context.feed_aliases,
            )
        elif job.kind == "sync_prices":
            await sync_prices(
                context.session_factory,
                context.prices_http,
                job,
                today=context.today(),
                history_days=context.prices_history_days,
                per_run=context.prices_per_run,
                pause_seconds=context.prices_pause_seconds,
                cooldown_minutes=context.prices_cooldown_minutes,
                max_seconds=context.lease_seconds * LEASE_SHARE,
                sleep=context.prices_sleep,
                clock=context.prices_clock,
            )
        elif job.kind == "extract_document" and context.llm is None:
            raise JobCannotSucceed("extraction is switched off (EXTRACTION_ENABLED)")
        elif job.kind == "extract_document" and context.llm is not None:
            await extract_document(
                context.session_factory,
                context.llm,
                job,
                version=context.extraction_version,
                budget_usd=context.extraction_budget_usd,
                prices=(context.llm_input_usd_per_mtok, context.llm_output_usd_per_mtok),
                concurrency=context.extraction_concurrency,
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


async def _queue_extractions(context: WorkerContext, model: str) -> None:
    try:
        async with context.session_factory() as db:
            queued = await enqueue_extractions(db, version=context.extraction_version, model=model)
            await db.commit()
        if queued:
            logger.info("extractions_queued", extra={"documents": queued})
    except Exception:
        logger.exception("extractions_queue_error")


async def _queue_feed_poll(context: WorkerContext, every_minutes: int) -> None:
    try:
        async with context.session_factory() as db:
            queued = await enqueue_poll(db, every_minutes=every_minutes)
            await db.commit()
        if queued:
            logger.info("feed_poll_queued", extra={"mode": context.feed_mode})
    except Exception:
        logger.exception("feed_poll_queue_error")


async def _queue_price_sync(context: WorkerContext, every_minutes: int) -> None:
    try:
        async with context.session_factory() as db:
            queued = await enqueue_sync(
                db,
                every_minutes=every_minutes,
                history_days=context.prices_history_days,
                today=context.today(),
            )
            await db.commit()
        if queued:
            logger.info("price_sync_queued")
    except Exception:
        logger.exception("price_sync_queue_error")


async def run_forever(
    context: WorkerContext,
    stop: asyncio.Event,
    *,
    poll_seconds: float,
    discovery_every_hours: int | None = None,
    embed_model: str | None = None,
    embed_check_seconds: float = EMBED_CHECK_SECONDS,
    extract_check_seconds: float = EXTRACT_CHECK_SECONDS,
    feed_poll_minutes: int | None = None,
    feed_check_seconds: float = FEED_CHECK_SECONDS,
    prices_run_minutes: int | None = None,
    prices_check_seconds: float = PRICES_CHECK_SECONDS,
) -> None:
    """Work through the queue until ``stop`` is set. The worker's small timers (ADR 008):

    - with ``discovery_every_hours``, queue a filing discovery per stock that often (ADR 018);
    - with ``embed_model``, every ``embed_check_seconds`` queue fingerprints for any ingested
      document still missing them under that model (P10);
    - with an LLM in the context, every ``extract_check_seconds`` queue a reading for facts and
      events of any ingested document this extractor version and model have not read (P11);
    - with ``feed_poll_minutes``, every ``feed_check_seconds`` make sure this time slot's RBI feed
      poll is queued (P15; why that is safe with several workers: app/feed_jobs.py);
    - with ``prices_run_minutes``, every ``prices_check_seconds`` make sure this time slot's price
      sync is queued when days are left to fetch (ADR 025; app/price_jobs.py).
    """
    next_discovery_check = 0.0
    next_embed_check = 0.0
    next_extract_check = 0.0
    next_feed_check = 0.0
    next_prices_check = 0.0
    while True:
        if discovery_every_hours and time.monotonic() >= next_discovery_check:
            await _queue_discovery(context, discovery_every_hours)
            next_discovery_check = time.monotonic() + DISCOVERY_CHECK_SECONDS
        if embed_model and time.monotonic() >= next_embed_check:
            await _queue_embeddings(context, embed_model)
            next_embed_check = time.monotonic() + embed_check_seconds
        if context.llm is not None and time.monotonic() >= next_extract_check:
            await _queue_extractions(context, context.llm.model)
            next_extract_check = time.monotonic() + extract_check_seconds
        if feed_poll_minutes and time.monotonic() >= next_feed_check:
            await _queue_feed_poll(context, feed_poll_minutes)
            next_feed_check = time.monotonic() + feed_check_seconds
        if prices_run_minutes and time.monotonic() >= next_prices_check:
            await _queue_price_sync(context, prices_run_minutes)
            next_prices_check = time.monotonic() + prices_check_seconds
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
    # And the LLM: without EXTRACTION_ENABLED there is no client, so no filing is read or paid for.
    llm = (
        BedrockLlm(model=settings.llm_model, client=bedrock_llm_client(settings.aws_region))
        if settings.extraction_enabled
        else None
    )
    # And RBI: a client exists only in live mode; fixture mode reads files shipped with the code.
    feed_http = httpx.AsyncClient(timeout=30.0) if settings.feed_mode == "live" else None
    # And BSE's price files: a client exists only with PRICES_ENABLED.
    prices_http = httpx.AsyncClient(timeout=60.0) if settings.prices_enabled else None
    context = WorkerContext(
        session_factory=create_session_factory(engine),
        blob_store=make_blob_store(settings),
        lease_seconds=settings.job_lease_seconds,
        http=http,
        filings_max_bytes=settings.filings_max_bytes,
        filings_years=settings.filings_years,
        embedder=embedder,
        embedding_token_budget=settings.embedding_token_budget,
        embedding_concurrency=settings.embedding_concurrency,
        llm=llm,
        extraction_budget_usd=settings.extraction_budget_usd,
        llm_input_usd_per_mtok=settings.llm_input_usd_per_mtok,
        llm_output_usd_per_mtok=settings.llm_output_usd_per_mtok,
        extraction_concurrency=settings.extraction_concurrency,
        feed_mode=settings.feed_mode,
        feed_http=feed_http,
        prices_http=prices_http,
        prices_history_days=settings.prices_history_days,
        prices_per_run=settings.prices_per_run,
        prices_pause_seconds=settings.prices_pause_seconds,
        prices_cooldown_minutes=settings.prices_cooldown_minutes,
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
            feed_poll_minutes=settings.feed_poll_minutes,
            prices_run_minutes=settings.prices_run_minutes if prices_http else None,
        )
    finally:
        if http is not None:
            await http.aclose()
        if feed_http is not None:
            await feed_http.aclose()
        if prices_http is not None:
            await prices_http.aclose()
        await engine.dispose()
        logger.info("worker_stopped")


if __name__ == "__main__":  # pragma: no cover - the process entrypoint
    asyncio.run(_main())
