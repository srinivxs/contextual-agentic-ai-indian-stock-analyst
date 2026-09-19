"""Application factory.

Run with ``uvicorn app.main:create_app --factory --app-dir src``. Using a factory (instead of a
module-level ``app``) means importing this module has no side effects, and tests can build as
many isolated apps as they like.
"""

from fastapi import FastAPI

from app import __version__
from app.api import health
from app.api.middleware import RequestContextMiddleware
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Contextual Agentic AI Indian Stock Analyst API",
        version=__version__,
        # Under /api so CloudFront, which will only forward /api/*, can reach the docs too.
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health.router)
    return app
