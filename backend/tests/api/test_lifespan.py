"""The engine is built with the app and disposed on shutdown: one engine per process."""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.main import create_app
from tests.helpers import build_settings


def test_create_app_builds_one_engine_and_one_session_factory() -> None:
    app = create_app(build_settings())

    assert isinstance(app.state.engine, AsyncEngine)
    assert isinstance(app.state.session_factory, async_sessionmaker)
    assert app.state.session_factory.kw["bind"] is app.state.engine


def test_each_app_gets_its_own_engine() -> None:
    first, second = create_app(build_settings()), create_app(build_settings())
    assert first.state.engine is not second.state.engine


async def test_shutdown_disposes_the_engine_so_pooled_connections_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disposed: list[AsyncEngine] = []

    async def record_dispose(self: AsyncEngine) -> None:
        disposed.append(self)

    # AsyncEngine forbids setting attributes on an instance, so the class is patched instead.
    monkeypatch.setattr(AsyncEngine, "dispose", record_dispose)
    app = create_app(build_settings())

    async with app.router.lifespan_context(app):
        assert disposed == []  # still serving: the pool must stay open

    assert disposed == [app.state.engine]
