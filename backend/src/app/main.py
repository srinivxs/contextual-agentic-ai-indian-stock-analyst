"""Application factory.

Run with ``uvicorn app.main:create_app --factory --app-dir src``. Using a factory (instead of a
module-level ``app``) means importing this module has no side effects, and tests can build as
many isolated apps as they like.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app import __version__
from app.api import auth, documents, health, stocks
from app.api.middleware import NoStoreMiddleware, RequestContextMiddleware
from app.auth.jwks import JwksCache
from app.blobs import FilesystemBlobStore
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.db.engine import create_db_engine, create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    # One engine (and so one connection pool) per process. Building it opens no connection.
    engine = create_db_engine(settings)
    # One HTTP client for all outbound calls to Google, so connections are reused rather than
    # dialled per login, and one JWKS cache sharing it (see app/auth/jwks.py).
    http_client = httpx.AsyncClient(timeout=settings.google_timeout_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        # Shutdown: close the pooled database connections and the outbound HTTP client.
        await engine.dispose()
        await http_client.aclose()

    app = FastAPI(
        title="Contextual Agentic AI Indian Stock Analyst API",
        version=__version__,
        lifespan=lifespan,
        # Under /api so CloudFront, which will only forward /api/*, can reach the docs too.
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.http_client = http_client
    app.state.jwks = JwksCache(client=http_client)
    # Uploaded files. Constructing it touches no disk; the folder appears on the first upload.
    app.state.blob_store = FilesystemBlobStore(settings.blob_root)

    app.add_middleware(NoStoreMiddleware)
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(stocks.router)
    app.include_router(documents.router)
    return app
