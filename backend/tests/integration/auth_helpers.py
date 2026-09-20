"""Helpers shared by the auth integration tests (fixtures live in conftest.py)."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.auth.sessions import create_session

SEVEN_DAYS = timedelta(days=7)


async def open_session(
    factory: async_sessionmaker[AsyncSession], user_id: UUID, lifetime: timedelta = SEVEN_DAYS
) -> str:
    """Create and COMMIT a session, exactly as the login code will (functions never commit)."""
    async with factory() as db:
        value = await create_session(db, user_id=user_id, lifetime=lifetime)
        await db.commit()
    return value


async def age_all_sessions(admin_engine: AsyncEngine) -> None:
    """Make every session look like it was created 8 days ago and expired yesterday."""
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE sessions SET created_at = now() - interval '8 days', "
                "expires_at = now() - interval '1 day'"
            )
        )


async def count_sessions(admin_engine: AsyncEngine) -> int:
    async with admin_engine.connect() as connection:
        count: int = (await connection.execute(text("SELECT count(*) FROM sessions"))).scalar_one()
        return count
