"""Application factory.

Run with ``uvicorn app.main:create_app --factory --app-dir src``. Using a factory (instead of a
module-level ``app``) means importing this module has no side effects, and tests can build as
many isolated apps as they like.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api import health
from app.api.middleware import RequestContextMiddleware
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.db.engine import create_db_engine, create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    # One engine (and so one connection pool) per process. Building it opens no connection.
    engine = create_db_engine(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()  # shutdown: close the pooled connections cleanly

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

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health.router)
    return app
