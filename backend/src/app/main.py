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
from app.api import (
    auth,
    chat,
    data,
    documents,
    feed,
    fundamentals,
    health,
    insights,
    match,
    prices,
    profile,
    search,
    series,
    stocks,
)
from app.api.middleware import NoStoreMiddleware, RequestContextMiddleware
from app.auth.jwks import JwksCache
from app.chat.graph import GraphChatEngine
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.db.engine import create_db_engine, create_session_factory
from app.embeddings import BedrockEmbedder, bedrock_client
from app.llm import BedrockLlm, bedrock_llm_client


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
    # Search turns each question into a fingerprint on Bedrock (P10b). Without the switch there is
    # no client at all, so nothing here can call AWS or spend money. Building it makes no request.
    app.state.embedder = (
        BedrockEmbedder(model=settings.embedding_model, client=bedrock_client(settings.aws_region))
        if settings.embeddings_enabled
        else None
    )
    # The grounded chat (P12): the LangGraph workflow, only when CHAT_ENABLED is on. Without it,
    # asking answers 409 "Chat is switched off" and nothing can call an LLM or spend. It searches
    # with the embedder above when search is on, and answers from the facts alone when it is not.
    # Tests put a fake engine here (tests/fake_chat.py).
    app.state.chat_engine = (
        GraphChatEngine(
            session_factory=app.state.session_factory,
            embedder=app.state.embedder,
            llm=BedrockLlm(
                model=settings.llm_model, client=bedrock_llm_client(settings.aws_region)
            ),
        )
        if settings.chat_enabled
        else None
    )

    app.add_middleware(NoStoreMiddleware)
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(stocks.router)
    app.include_router(documents.router)
    app.include_router(data.router)
    app.include_router(search.router)
    app.include_router(insights.router)
    app.include_router(series.router)
    app.include_router(prices.router)
    app.include_router(fundamentals.router)
    app.include_router(chat.router)
    app.include_router(profile.router)
    app.include_router(feed.router)
    app.include_router(match.router)
    return app
