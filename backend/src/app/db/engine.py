"""Database access: one engine per process, one short-lived session per request.

* The **engine** owns the connection pool. Opening a PostgreSQL connection is slow (TCP, TLS,
  authentication, a server process), so connections are kept and reused. Creating an engine does no
  I/O: the first connection is opened by the first query, which is why the app can start while the
  database is down and ``/api/readyz`` can report that truthfully.
* A **session** borrows one connection from the pool while it is in use and returns it when it is
  closed. Sessions are not safe to share between concurrent requests, so each request gets its own.
"""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


def create_db_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        # A connection killed by a database restart or an idle cut-off is detected with a cheap
        # ping before it is handed out, then replaced, instead of failing a real request.
        pool_pre_ping=True,
        connect_args={"timeout": settings.db_connect_timeout_seconds},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: after a commit, attributes must stay readable. The default would
    # expire them, and reloading them lazily is impossible under asyncio.
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: a session for this request, closed (and its connection returned) after.

    Nothing commits automatically: code that writes must call ``await session.commit()``, so
    transaction boundaries stay visible. An uncommitted session is rolled back when it closes.
    """
    async with request.app.state.session_factory() as session:
        yield session
