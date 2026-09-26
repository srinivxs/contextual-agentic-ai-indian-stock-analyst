"""Revision 0006: the embeddings table and the embed_document job kind (P10).

One fingerprint per (model, text): keyed by the chunk text's SHA-256, not by the chunk, so identical
text in two filings is embedded once, and re-ingesting a document costs nothing. The model is part
of the key so fingerprints of two different models can never be compared by mistake.
"""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import Migrator

pytestmark = pytest.mark.usefixtures("migrated_db", "clean_document_tables")

HASH = "ab" * 32
VECTOR = "[" + ",".join(["0.03125"] * 1024) + "]"


async def insert(engine: AsyncEngine, **overrides: Any) -> None:
    values = {"model": "m", "hash": HASH, "vector": VECTOR, "tokens": 5, **overrides}
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO embeddings (model, content_hash, embedding, input_tokens) "
                "VALUES (:model, :hash, CAST(:vector AS vector), :tokens)"
            ),
            values,
        )


async def count(engine: AsyncEngine) -> int:
    async with engine.connect() as connection:
        found: int = (
            await connection.execute(text("SELECT count(*) FROM embeddings"))
        ).scalar_one()
        return found


async def test_the_runtime_role_can_store_and_read_a_fingerprint(app_engine: AsyncEngine) -> None:
    """The worker and the api connect as the no-DDL runtime role; default privileges cover the
    new table (ADR 011)."""
    await insert(app_engine)
    async with app_engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT model, input_tokens, vector_dims(embedding) AS dims FROM embeddings")
            )
        ).one()
    assert (row.model, row.input_tokens, row.dims) == ("m", 5, 1024)


async def test_one_fingerprint_per_model_and_text(admin_engine: AsyncEngine) -> None:
    await insert(admin_engine)
    await insert(admin_engine, model="another-model")  # the same text under another model is fine
    with pytest.raises(IntegrityError):
        await insert(admin_engine)
    assert await count(admin_engine) == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"vector": "[1,2,3]"},  # the column only takes 1,024 numbers
        {"tokens": -1},
        {"hash": "not-a-hash"},
        {"model": ""},
    ],
    ids=["wrong-dimensions", "negative-tokens", "bad-hash", "empty-model"],
)
async def test_a_bad_row_is_refused_by_the_database(
    admin_engine: AsyncEngine, overrides: dict[str, Any]
) -> None:
    with pytest.raises(DBAPIError):
        await insert(admin_engine, **overrides)
    assert await count(admin_engine) == 0


async def test_the_queue_accepts_embed_document_jobs(admin_engine: AsyncEngine) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, payload, dedupe_key) "
                "VALUES ('embed_document', '{\"document_id\": 1}', 'embed_document:1')"
            )
        )


async def test_downgrading_removes_the_table_and_the_job_kind(
    migrator: Migrator, admin_engine: AsyncEngine
) -> None:
    await insert(admin_engine)
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs (kind, payload, dedupe_key) "
                "VALUES ('embed_document', '{\"document_id\": 1}', 'embed_document:1')"
            )
        )

    await migrator.downgrade("0005")
    try:
        async with admin_engine.connect() as connection:
            table = (
                await connection.execute(text("SELECT to_regclass('public.embeddings')"))
            ).scalar_one()
            jobs = (await connection.execute(text("SELECT count(*) FROM jobs"))).scalar_one()
        assert table is None
        assert jobs == 0  # the kind 0005 does not know is gone with it
        with pytest.raises(IntegrityError):
            async with admin_engine.begin() as connection:
                await connection.execute(
                    text("INSERT INTO jobs (kind, dedupe_key) VALUES ('embed_document', 'x')")
                )
    finally:
        await migrator.upgrade("head")
