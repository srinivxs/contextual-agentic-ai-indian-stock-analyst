"""Making the fingerprints: the embed_document job and the timer that queues it (P10).

    timer -> embed_document(document) -> every chunk text of it with no fingerprint yet
          -> the embedder (Bedrock in real runs, a fake here) -> embeddings (model, content_hash)

What is checked: only ingested documents are queued, each distinct text is paid for once (even
across documents), running again costs nothing, two workers at once create no duplicates, the
token cap stops spending, a transient failure is retried, and with the switch off nothing runs.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.blobs import FilesystemBlobStore
from app.embedding_jobs import EMBED_BATCH, RETRY_MINUTES, enqueue_embeddings
from app.jobs import claim_next
from app.worker import WorkerContext, handle, run_forever, run_once
from tests.fakes import FakeEmbedder
from tests.pdfs import DEMOCO_RESULTS

pytestmark = pytest.mark.usefixtures("migrated_db", "clean_document_tables")

Factory = async_sessionmaker[AsyncSession]
MODEL = FakeEmbedder.model


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def add_document(
    engine: AsyncEngine, number: int, texts: list[str], *, status: str = "completed"
) -> int:
    """A document as the ingestion job leaves it: its chunks stored, its status set."""
    digest = f"{number:064x}"
    async with engine.begin() as connection:
        document_id: int = (
            await connection.execute(
                text(
                    "INSERT INTO documents (stock_id, title, sha256, size_bytes, blob_key, source, "
                    "source_url, status, kind, period) "
                    "SELECT id, 'DemoCo filing', :sha, 10, 'documents/' || :sha || '.pdf', 'bse', "
                    ":url, :status, 'announcement', 'DemoCo filing' "
                    "FROM stocks WHERE symbol = 'TCS' RETURNING id"
                ),
                {
                    "sha": digest,
                    "status": status,
                    "url": f"https://www.bseindia.com/xml-data/corpfiling/AttachHis/{number}.pdf",
                },
            )
        ).scalar_one()
        for ordinal, chunk in enumerate(texts):
            await connection.execute(
                text(
                    "INSERT INTO chunks (document_id, ordinal, page_number, text, content_hash) "
                    "VALUES (:document, :ordinal, 1, :text, :hash)"
                ),
                {"document": document_id, "ordinal": ordinal, "text": chunk, "hash": sha(chunk)},
            )
    return document_id


async def add_fingerprint(
    engine: AsyncEngine, chunk: str, *, model: str = MODEL, tokens: int = 1
) -> None:
    vector = "[" + ",".join(["0.03125"] * 1024) + "]"
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO embeddings (model, content_hash, embedding, input_tokens) "
                "VALUES (:model, :hash, CAST(:vector AS vector), :tokens)"
            ),
            {"model": model, "hash": sha(chunk), "vector": vector, "tokens": tokens},
        )


async def rows(engine: AsyncEngine, sql: str) -> list[tuple[Any, ...]]:
    async with engine.connect() as connection:
        return [tuple(row) for row in await connection.execute(text(sql))]


async def queue(session_factory: Factory) -> int:
    async with session_factory() as db:
        queued = await enqueue_embeddings(db, model=MODEL)
        await db.commit()
    return queued


def context_for(
    session_factory: Factory,
    tmp_path: Path,
    embedder: FakeEmbedder | None,
    *,
    budget: int = 1_000_000,
) -> WorkerContext:
    return WorkerContext(
        session_factory=session_factory,
        blob_store=FilesystemBlobStore(tmp_path / "blobs"),
        lease_seconds=300,
        embedder=embedder,
        embedding_token_budget=budget,
        embedding_concurrency=2,
    )


async def drain(context: WorkerContext) -> None:
    while await run_once(context):
        pass


# --- the timer ------------------------------------------------------------------------------------


async def test_the_timer_queues_each_ingested_document_that_lacks_fingerprints(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    wanted = await add_document(admin_engine, 1, ["DemoCo revenue grew"])
    await add_document(admin_engine, 2, ["still being read"], status="processing")
    await add_document(admin_engine, 3, ["could not be read"], status="failed")
    await add_document(admin_engine, 4, ["already fingerprinted"])
    await add_fingerprint(admin_engine, "already fingerprinted")

    assert await queue(session_factory) == 1
    assert await queue(session_factory) == 0  # already queued: one live job per document

    assert await rows(admin_engine, "SELECT kind, payload, dedupe_key FROM jobs") == [
        ("embed_document", {"document_id": wanted}, f"embed_document:{wanted}")
    ]


async def test_fingerprints_of_another_model_do_not_count(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """Vectors of two models cannot be compared, so a model change means embedding again."""
    await add_document(admin_engine, 1, ["DemoCo revenue grew"])
    await add_fingerprint(admin_engine, "DemoCo revenue grew", model="some-older-model")

    assert await queue(session_factory) == 1


async def test_after_a_failure_the_document_waits_before_it_is_queued_again(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """Expired credentials or a used-up cap would otherwise fail again every minute."""
    await add_document(admin_engine, 1, ["DemoCo revenue grew"])
    assert await queue(session_factory) == 1
    async with admin_engine.begin() as connection:
        await connection.execute(text("UPDATE jobs SET status = 'failed'"))

    assert await queue(session_factory) == 0
    async with admin_engine.begin() as connection:
        await connection.execute(
            text("UPDATE jobs SET created_at = now() - make_interval(mins => :m)"),
            {"m": RETRY_MINUTES + 1},
        )
    assert await queue(session_factory) == 1


# --- the job --------------------------------------------------------------------------------------


async def test_each_distinct_text_gets_one_fingerprint_with_its_token_count(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    await add_document(
        admin_engine, 1, ["DemoCo revenue grew", "DemoCo revenue grew", "margins fell sharply"]
    )
    embedder = FakeEmbedder()
    await queue(session_factory)

    await drain(context_for(session_factory, tmp_path, embedder))

    assert sorted(embedder.calls) == ["DemoCo revenue grew", "margins fell sharply"]
    stored = await rows(
        admin_engine,
        "SELECT model, content_hash, input_tokens, vector_dims(embedding) FROM embeddings",
    )
    assert sorted(stored) == sorted(
        [
            (MODEL, sha("DemoCo revenue grew"), 3, 1024),
            (MODEL, sha("margins fell sharply"), 3, 1024),
        ]
    )
    assert await rows(admin_engine, "SELECT status FROM jobs") == [("completed",)]


async def test_text_already_fingerprinted_elsewhere_is_not_paid_for_again(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    await add_document(admin_engine, 1, ["a standard DemoCo disclaimer"])
    await add_fingerprint(admin_engine, "a standard DemoCo disclaimer")
    await add_document(admin_engine, 2, ["a standard DemoCo disclaimer", "new DemoCo guidance"])
    embedder = FakeEmbedder()
    await queue(session_factory)

    await drain(context_for(session_factory, tmp_path, embedder))

    assert embedder.calls == ["new DemoCo guidance"]


async def test_running_the_job_again_embeds_nothing(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    document = await add_document(admin_engine, 1, ["DemoCo revenue grew"])
    await queue(session_factory)
    await drain(context_for(session_factory, tmp_path, FakeEmbedder()))

    again = FakeEmbedder()
    async with admin_engine.begin() as connection:  # a second job for the same document
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('embed_document', "
                "jsonb_build_object('document_id', CAST(:id AS bigint)), 'embed_document:' || :id)"
            ),
            {"id": document},
        )
    await drain(context_for(session_factory, tmp_path, again))

    assert again.calls == []
    assert await rows(admin_engine, "SELECT count(*) FROM embeddings") == [(1,)]
    assert await rows(admin_engine, "SELECT DISTINCT status FROM jobs") == [("completed",)]


async def test_two_workers_at_once_create_no_duplicates_and_no_errors(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    """Two documents with the same texts, embedded by two workers at the same moment: both may
    pay for a text, but ON CONFLICT keeps one row per text and neither job fails."""
    texts = [f"DemoCo shared paragraph {n}" for n in range(10)]
    await add_document(admin_engine, 1, texts)
    await add_document(admin_engine, 2, texts)
    await queue(session_factory)
    context = context_for(session_factory, tmp_path, FakeEmbedder())

    await asyncio.gather(run_once(context), run_once(context))

    assert await rows(admin_engine, "SELECT count(*) FROM embeddings") == [(10,)]
    assert await rows(admin_engine, "SELECT status FROM jobs ORDER BY id") == [
        ("completed",),
        ("completed",),
    ]


# --- the spending cap -----------------------------------------------------------------------------


async def test_with_the_cap_used_up_nothing_is_spent_and_the_job_says_why(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    await add_fingerprint(admin_engine, "earlier work", tokens=100)
    await add_document(admin_engine, 1, ["DemoCo revenue grew"])
    embedder = FakeEmbedder()
    await queue(session_factory)

    await drain(context_for(session_factory, tmp_path, embedder, budget=100))

    assert embedder.calls == []
    [(status, error)] = await rows(admin_engine, "SELECT status, last_error FROM jobs")
    assert status == "failed"
    assert "EMBEDDING_TOKEN_BUDGET" in error


async def test_reaching_the_cap_midway_keeps_what_was_done_and_stops(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    texts = [f"DemoCo paragraph number {n}" for n in range(EMBED_BATCH + 8)]  # 4 tokens each
    await add_document(admin_engine, 1, texts)
    embedder = FakeEmbedder()
    await queue(session_factory)

    await drain(context_for(session_factory, tmp_path, embedder, budget=50))

    assert len(embedder.calls) == EMBED_BATCH  # one batch, then the cap was checked again
    assert await rows(admin_engine, "SELECT count(*) FROM embeddings") == [(EMBED_BATCH,)]
    assert await rows(admin_engine, "SELECT status FROM jobs") == [("failed",)]


# --- failures -------------------------------------------------------------------------------------


async def test_a_transient_failure_is_retried_and_later_finishes(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    """Throttling or an expired pass: the job goes back in the queue with a backoff."""
    await add_document(admin_engine, 1, ["DemoCo revenue grew", "a throttled paragraph"])
    await queue(session_factory)

    await run_once(context_for(session_factory, tmp_path, FakeEmbedder(fail_on="throttled")))
    [(status, error)] = await rows(admin_engine, "SELECT status, last_error FROM jobs")
    assert status == "pending"
    assert "RuntimeError" in error

    async with admin_engine.begin() as connection:  # the backoff has passed
        await connection.execute(text("UPDATE jobs SET run_after = now()"))
    await drain(context_for(session_factory, tmp_path, FakeEmbedder()))
    assert await rows(admin_engine, "SELECT status FROM jobs") == [("completed",)]
    assert await rows(admin_engine, "SELECT count(*) FROM embeddings") == [(2,)]


async def test_with_embeddings_switched_off_the_job_fails_without_retrying(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    await add_document(admin_engine, 1, ["DemoCo revenue grew"])
    await queue(session_factory)

    await drain(context_for(session_factory, tmp_path, None))

    [(status, error)] = await rows(admin_engine, "SELECT status, last_error FROM jobs")
    assert status == "failed"
    assert "EMBEDDINGS_ENABLED" in error
    assert await rows(admin_engine, "SELECT count(*) FROM embeddings") == [(0,)]


async def test_a_document_no_longer_ingested_is_not_embedded(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    await add_document(admin_engine, 1, ["DemoCo revenue grew"])
    await queue(session_factory)
    async with admin_engine.begin() as connection:  # being read again, say
        await connection.execute(text("UPDATE documents SET status = 'processing'"))
    embedder = FakeEmbedder()

    await drain(context_for(session_factory, tmp_path, embedder))

    assert embedder.calls == []
    assert await rows(admin_engine, "SELECT status FROM jobs") == [("failed",)]


async def test_a_worker_that_lost_its_lease_writes_nothing(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    await add_document(admin_engine, 1, ["DemoCo revenue grew"])
    await queue(session_factory)
    async with session_factory() as db:
        job = await claim_next(db, lease_seconds=300)
        await db.commit()
    assert job is not None
    async with admin_engine.begin() as connection:  # another worker claimed it meanwhile
        await connection.execute(text("UPDATE jobs SET attempts = attempts + 1"))

    await handle(context_for(session_factory, tmp_path, FakeEmbedder()), job)

    assert await rows(admin_engine, "SELECT count(*) FROM embeddings") == [(0,)]


# --- end to end -----------------------------------------------------------------------------------


async def test_a_filing_is_read_then_fingerprinted_without_anyone_asking(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    """The worker loop alone: ingest the PDF, then the timer notices the new chunks and queues
    their fingerprints."""
    from tests.integration.test_ingest_db import seed

    context = context_for(session_factory, tmp_path, FakeEmbedder())
    await seed(DEMOCO_RESULTS, session_factory, context.blob_store)  # type: ignore[arg-type]
    stop = asyncio.Event()

    async def until_fingerprinted() -> None:
        for _ in range(400):  # the outer timeout ends a run that never gets there
            if (await rows(admin_engine, "SELECT count(*) FROM embeddings"))[0][0] > 0:
                break
            await asyncio.sleep(0.05)
        stop.set()

    await asyncio.wait_for(
        asyncio.gather(
            run_forever(context, stop, poll_seconds=0.01, embed_model=MODEL, embed_check_seconds=0),
            until_fingerprinted(),
        ),
        timeout=20,
    )

    chunks = await rows(admin_engine, "SELECT count(DISTINCT content_hash) FROM chunks")
    assert await rows(admin_engine, "SELECT count(*) FROM embeddings") == chunks


async def test_a_worker_that_lost_its_lease_does_not_mark_the_job_done(
    session_factory: Factory, admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    """Nothing left to embed, but the job is someone else's now: they complete it, not us."""
    document = await add_document(admin_engine, 1, ["DemoCo revenue grew"])
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('embed_document', "
                "jsonb_build_object('document_id', CAST(:id AS bigint)), 'embed_document:' || :id)"
            ),
            {"id": document},
        )
    await add_fingerprint(admin_engine, "DemoCo revenue grew")
    async with session_factory() as db:
        job = await claim_next(db, lease_seconds=300)
        await db.commit()
    assert job is not None
    async with admin_engine.begin() as connection:
        await connection.execute(text("UPDATE jobs SET attempts = attempts + 1"))

    await handle(context_for(session_factory, tmp_path, FakeEmbedder()), job)

    assert await rows(admin_engine, "SELECT status FROM jobs") == [("processing",)]


async def test_a_database_error_while_queueing_fingerprints_does_not_stop_the_worker(
    tmp_path: Path,
) -> None:
    calls = 0
    stop = asyncio.Event()

    def broken_session() -> Any:
        nonlocal calls
        calls += 1
        stop.set()
        raise ConnectionError("database unavailable")

    context = WorkerContext(broken_session, FilesystemBlobStore(tmp_path), 300)  # type: ignore[arg-type]
    await asyncio.wait_for(
        run_forever(context, stop, poll_seconds=0.01, embed_model=MODEL), timeout=5
    )
    assert calls >= 1
