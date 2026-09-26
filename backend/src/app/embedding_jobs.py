"""Making the fingerprints for a document's chunks: the embed_document job, and the timer that
queues it (P10, ADR 019).

    worker timer ──> embed_document(document) for each ingested document with a chunk text that has
                     no fingerprint yet for this model
    embed_document ──> those texts, EMBED_BATCH at a time ──> the embedder (Bedrock) ──> embeddings

WHY A TIMER, NOT "INGEST THEN QUEUE": one query finds every document that still needs work, whatever
the reason: a new filing, the first run over the existing ones, a model change, a job that stopped
at the spending cap or on an expired pass. Nothing has to remember to queue anything.

HOW DUPLICATES ARE STOPPED: the same as ingestion. The job reads which texts are missing, pays for
those only, and stores them with INSERT ... ON CONFLICT (model, content_hash) DO NOTHING, so a
repeat run, or two workers at once, leave exactly one row per text. No transaction is held open
across a Bedrock call: read in one short transaction, call with none, write in another.

THE SPENDING CAP: before each batch the job adds up the tokens stored for this model; at or over
EMBEDDING_TOKEN_BUDGET it stops, keeps what it made, and fails with a message saying so. The cap
can be overshot by at most one batch (about 32 x 300 tokens, a hundredth of a cent).
"""

import asyncio
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import CursorResult, Row, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.embeddings import Embedder, Embedding, vector_literal
from app.ingest import JobCannotSucceed, document_id_of
from app.jobs import ClaimedJob, complete, still_mine

# Texts embedded per round trip to the database: small enough that a crash loses little work.
EMBED_BATCH = 32
# After a job for a document is queued, how long before the timer may queue it again. A job that
# failed (an expired pass, the cap reached) is retried after this, not every minute.
RETRY_MINUTES = 10

_ENQUEUE = text(
    """
    INSERT INTO jobs (kind, payload, dedupe_key)
    SELECT 'embed_document',
           jsonb_build_object('document_id', d.id),
           'embed_document:' || d.id
    FROM documents d
    WHERE d.status = 'completed'
      AND EXISTS (
        SELECT 1 FROM chunks c
        WHERE c.document_id = d.id
          AND NOT EXISTS (
            SELECT 1 FROM embeddings e
            WHERE e.model = :model AND e.content_hash = c.content_hash
          )
      )
      AND NOT EXISTS (
        SELECT 1 FROM jobs j
        WHERE j.dedupe_key = 'embed_document:' || d.id
          AND j.created_at > now() - make_interval(mins => :minutes)
      )
    ORDER BY d.id
    ON CONFLICT (dedupe_key) WHERE status IN ('pending', 'processing') DO NOTHING
    """
)

_STATUS = text("SELECT status FROM documents WHERE id = :id")

# One row per distinct text: a paragraph repeated inside the document is paid for once.
_MISSING = text(
    """
    SELECT DISTINCT ON (c.content_hash) c.content_hash, c.text
    FROM chunks c
    WHERE c.document_id = :id
      AND NOT EXISTS (
        SELECT 1 FROM embeddings e WHERE e.model = :model AND e.content_hash = c.content_hash
      )
    ORDER BY c.content_hash
    """
)

_SPENT = text("SELECT coalesce(sum(input_tokens), 0) FROM embeddings WHERE model = :model")

_INSERT = text(
    "INSERT INTO embeddings (model, content_hash, embedding, input_tokens) "
    "VALUES (:model, :hash, CAST(:vector AS vector), :tokens) "
    "ON CONFLICT (model, content_hash) DO NOTHING"
)


async def enqueue_embeddings(
    db: AsyncSession, *, model: str, retry_minutes: int = RETRY_MINUTES
) -> int:
    """Queue an embed_document job for each ingested document still missing fingerprints."""
    params = {"model": model, "minutes": retry_minutes}
    result = cast("CursorResult[Any]", await db.execute(_ENQUEUE, params))
    return result.rowcount


def _budget_error(budget: int) -> JobCannotSucceed:
    return JobCannotSucceed(
        f"the spending cap is used up: {budget} tokens (EMBEDDING_TOKEN_BUDGET); raise it to go on"
    )


async def _embed_all(
    embedder: Embedder, texts: Sequence[str], concurrency: int
) -> list[Embedding | BaseException]:
    """A batch of texts, at most ``concurrency`` calls at a time. Each result is a fingerprint or
    the error for that text: one refused call must not throw away the others, already paid for."""
    gate = asyncio.Semaphore(concurrency)

    async def one(value: str) -> Embedding:
        async with gate:
            return await embedder.embed(value)

    return list(await asyncio.gather(*(one(value) for value in texts), return_exceptions=True))


async def embed_document(
    session_factory: async_sessionmaker[AsyncSession],
    embedder: Embedder,
    job: ClaimedJob,
    *,
    budget_tokens: int,
    concurrency: int,
) -> None:
    document_id = document_id_of(job)
    async with session_factory() as db:
        status = (await db.execute(_STATUS, {"id": document_id})).scalar_one_or_none()
        if status != "completed":
            raise JobCannotSucceed("the document is not ingested (yet)")
        missing: list[Row[Any]] = list(
            await db.execute(_MISSING, {"id": document_id, "model": embedder.model})
        )
        spent = int((await db.execute(_SPENT, {"model": embedder.model})).scalar_one())

    for start in range(0, len(missing), EMBED_BATCH):
        if spent >= budget_tokens:
            raise _budget_error(budget_tokens)
        batch = missing[start : start + EMBED_BATCH]
        results = await _embed_all(embedder, [row.text for row in batch], concurrency)
        made = [
            (row, result)
            for row, result in zip(batch, results, strict=True)
            if isinstance(result, Embedding)
        ]
        async with session_factory() as db:
            if not await still_mine(db, job):
                return  # another worker has this job now; it will do the rest
            for row, embedding in made:
                await db.execute(
                    _INSERT,
                    {
                        "model": embedder.model,
                        "hash": row.content_hash,
                        "vector": vector_literal(embedding.vector),
                        "tokens": embedding.tokens,
                    },
                )
            await db.commit()
        spent += sum(embedding.tokens for _, embedding in made)
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            raise errors[0]  # kept what worked; the queue retries the rest later

    async with session_factory() as db:
        if await still_mine(db, job):
            await complete(db, job)
            await db.commit()
