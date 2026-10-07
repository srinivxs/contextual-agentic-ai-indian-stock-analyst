"""The worker's lanes (the owner, 2026-10-07): everything must be ready within an hour of demo-up.

One worker used to run one job at a time, so reading filings with the AI waited behind slow,
polite downloads from BSE. Now one WEB lane takes the jobs that reach BSE and screener.in (still
one request at a time) and the AI lanes take everything else, side by side. Each lane claims only
its own kinds of job, and SKIP LOCKED keeps two lanes off the same job.
"""

import asyncio
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.blobs import FilesystemBlobStore
from app.jobs import AI_KINDS, WEB_KINDS, claim_next
from app.worker import WorkerContext, run_forever

pytestmark = pytest.mark.usefixtures("migrated_db", "clean_document_tables")

Factory = async_sessionmaker[AsyncSession]


async def queue(engine: AsyncEngine, kind: str, key: str, payload: str = "{}") -> int:
    async with engine.begin() as connection:
        job_id: int = (
            await connection.execute(
                text(
                    "INSERT INTO jobs (kind, payload, dedupe_key) "
                    "VALUES (:kind, CAST(:payload AS jsonb), :key) RETURNING id"
                ),
                {"kind": kind, "payload": payload, "key": key},
            )
        ).scalar_one()
        return job_id


async def status(engine: AsyncEngine, job_id: int) -> str:
    async with engine.connect() as connection:
        found: str = (
            await connection.execute(text("SELECT status FROM jobs WHERE id = :id"), {"id": job_id})
        ).scalar_one()
        return found


async def claim(factory: Factory, kinds: tuple[str, ...]) -> Any:
    async with factory() as db:
        claimed = await claim_next(db, lease_seconds=300, kinds=kinds)
        await db.commit()
        return claimed


async def test_a_lane_claims_only_its_own_kinds_of_job(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    older = await queue(admin_engine, "ingest_document", "ingest_document:1")
    web = await queue(admin_engine, "sync_prices", "sync_prices:slot")

    taken_by_web = await claim(session_factory, WEB_KINDS)
    assert taken_by_web is not None
    assert taken_by_web.id == web  # the older AI job is not its kind

    taken_by_ai = await claim(session_factory, AI_KINDS)
    assert taken_by_ai is not None
    assert taken_by_ai.id == older

    assert await claim(session_factory, WEB_KINDS) is None


async def test_every_kind_of_job_belongs_to_exactly_one_lane(admin_engine: AsyncEngine) -> None:
    """A new kind of job added to the table but to no lane would never run: this catches it."""
    async with admin_engine.connect() as connection:
        definition: str = (
            await connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'ck_jobs_kind'"
                )
            )
        ).scalar_one()
    kinds = set(re.findall(r"'([a-z_]+)'", definition))
    assert set(WEB_KINDS) | set(AI_KINDS) == kinds
    assert not set(WEB_KINDS) & set(AI_KINDS)


async def test_the_ai_lane_keeps_working_while_the_web_lane_waits_on_bse(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    release = asyncio.Event()

    async def slow_bse(request: httpx.Request) -> httpx.Response:
        await release.wait()  # BSE is slow today: the price download hangs until released
        return httpx.Response(406)

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow_bse)) as http:
        context = WorkerContext(
            session_factory=session_factory,
            blob_store=FilesystemBlobStore(tmp_path / "blobs"),
            lease_seconds=300,
            prices_http=http,
            prices_per_run=1,
        )
        prices = await queue(admin_engine, "sync_prices", "sync_prices:slot")
        feed = await queue(admin_engine, "poll_feed", "poll_feed:rbi:test", '{"source": "rbi"}')

        stop = asyncio.Event()
        worker = asyncio.create_task(run_forever(context, stop, poll_seconds=0.01, ai_lanes=1))
        for _ in range(250):  # the RBI feed (sample items) is read while BSE is still hanging
            if await status(admin_engine, feed) == "completed":
                break
            await asyncio.sleep(0.02)

        assert await status(admin_engine, feed) == "completed"
        assert await status(admin_engine, prices) == "processing"

        release.set()
        stop.set()
        await asyncio.wait_for(worker, timeout=5)
    assert await status(admin_engine, prices) == "completed"
