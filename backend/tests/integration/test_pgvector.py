"""pgvector is installed by the migration and works for the runtime role. No vector columns yet."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.usefixtures("migrated_db")


async def test_the_vector_extension_is_installed_by_the_migration(
    admin_engine: AsyncEngine,
) -> None:
    async with admin_engine.connect() as connection:
        version = (
            await connection.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
        ).scalar_one()
    assert version  # e.g. "0.8.6"; the pinned image decides which


async def test_cosine_distance_works_for_the_runtime_role(app_engine: AsyncEngine) -> None:
    """The retrieval query in P10 is `ORDER BY embedding <=> query`: check that operator here."""
    async with app_engine.connect() as connection:
        distance = (
            await connection.execute(text("SELECT '[1,2,3]'::vector <=> '[3,2,1]'::vector"))
        ).scalar_one()
    # cosine similarity = (3 + 4 + 3) / 14, so distance = 1 - 10/14
    assert distance == pytest.approx(1 - 10 / 14, abs=1e-6)


async def test_a_1024_dimension_vector_is_accepted_and_other_sizes_are_rejected(
    app_engine: AsyncEngine,
) -> None:
    """ADR 010: Titan V2 produces 1024 numbers, so the future column is vector(1024)."""
    async with app_engine.connect() as connection:
        dimensions = (
            await connection.execute(
                text("SELECT vector_dims(array_fill(0.1::real, ARRAY[1024])::vector(1024))")
            )
        ).scalar_one()
        assert dimensions == 1024
    async with app_engine.connect() as connection:
        with pytest.raises(DBAPIError, match="expected 1024 dimensions"):
            await connection.execute(
                text("SELECT array_fill(0.1::real, ARRAY[1023])::vector(1024)")
            )


async def test_the_only_vector_column_is_the_fingerprint(admin_engine: AsyncEngine) -> None:
    """Scope guard: P10 adds exactly one vector column, embeddings.embedding (1,024 numbers)."""
    async with admin_engine.connect() as connection:
        columns = (
            await connection.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE udt_name = 'vector' ORDER BY 1, 2"
                )
            )
        ).all()
    assert [tuple(column) for column in columns] == [("embeddings", "embedding")]
