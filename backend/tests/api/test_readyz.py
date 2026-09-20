"""/api/readyz with a fake database session, and proof that /api/healthz never needs one."""

import asyncio
import io
import time
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from app.db.engine import get_session
from app.main import create_app
from tests.helpers import build_settings, parse_log_lines

SECRET_DETAIL = "host=db.internal.example password=hunter2"  # noqa: S105  (fake, must never leak)


class FakeSession:
    """Stands in for AsyncSession: runs `behaviour` when asked to execute a statement."""

    def __init__(self, behaviour: Callable[[], object]) -> None:
        self._behaviour = behaviour
        self.statements: list[str] = []

    async def execute(self, statement: object) -> None:
        self.statements.append(str(statement))
        result = self._behaviour()
        if asyncio.iscoroutine(result):
            await result


def use_session(app: FastAPI, session: FakeSession) -> None:
    async def override() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = override


def refused() -> None:
    raise OperationalError("SELECT 1", {}, Exception(SECRET_DETAIL))


async def hang() -> None:
    await asyncio.sleep(30)


async def test_ready_when_the_database_answers(app: FastAPI, client: httpx.AsyncClient) -> None:
    session = FakeSession(lambda: None)
    use_session(app, session)

    response = await client.get("/api/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert session.statements == ["SELECT 1"]  # a real round trip, not just "the app is up"


@pytest.mark.parametrize(
    "behaviour",
    [
        refused,
        lambda: (_ for _ in ()).throw(ConnectionRefusedError(SECRET_DETAIL)),
        lambda: (_ for _ in ()).throw(RuntimeError(SECRET_DETAIL)),
    ],
    ids=["sqlalchemy-error", "raw-os-error", "anything-else"],
)
async def test_not_ready_is_a_503_envelope_that_leaks_nothing(
    app: FastAPI, client: httpx.AsyncClient, behaviour: Callable[[], object]
) -> None:
    use_session(app, FakeSession(behaviour))

    response = await client.get("/api/readyz", headers={"X-Request-ID": "ready-fail-abcdef"})

    assert response.status_code == 503
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "not_ready"
    assert body["error"]["message"] == "Database is not ready"
    assert body["error"]["request_id"] == "ready-fail-abcdef"
    assert "db.internal.example" not in response.text
    assert "hunter2" not in response.text


async def test_a_hanging_database_cannot_hang_the_probe() -> None:
    app = create_app(build_settings(db_ready_timeout_seconds=0.05))
    use_session(app, FakeSession(hang))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    started = time.monotonic()
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/readyz")

    assert response.status_code == 503
    assert time.monotonic() - started < 2.0  # nowhere near the 30 s the fake would have slept


async def test_the_failure_is_logged_server_side_with_the_request_id(
    app: FastAPI, client: httpx.AsyncClient, log_stream: io.StringIO
) -> None:
    use_session(app, FakeSession(refused))

    await client.get("/api/readyz", headers={"X-Request-ID": "ready-log-abcdef"})

    lines = [r for r in parse_log_lines(log_stream) if r["message"] == "readiness_check_failed"]
    assert len(lines) == 1
    assert lines[0]["level"] == "WARNING"
    assert lines[0]["request_id"] == "ready-log-abcdef"


async def test_healthz_stays_up_and_never_touches_the_database(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """Liveness must survive a database outage, or the platform would kill healthy containers."""
    touched: list[str] = []

    async def exploding_session() -> AsyncIterator[None]:
        touched.append("get_session was called")
        raise RuntimeError(SECRET_DETAIL)
        yield  # pragma: no cover  (makes this an async generator, like the real dependency)

    app.dependency_overrides[get_session] = exploding_session

    response = await client.get("/api/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert touched == []
