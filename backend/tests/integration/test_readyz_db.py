"""/api/readyz against a real PostgreSQL: healthy, down, and wrongly configured."""

import time

from tests.helpers import running_app
from tests.integration.conftest import DbConfig


async def test_ready_when_the_real_database_is_up(db_config: DbConfig) -> None:
    async with running_app(db_config.settings()) as (_, client):
        response = await client.get("/api/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


async def test_not_ready_when_the_database_is_unreachable_but_still_alive(
    db_config: DbConfig,
) -> None:
    """Port 1 has no PostgreSQL. The app must start, report 503 fast, and stay live."""
    settings = db_config.settings(port=1, db_connect_timeout_seconds=1, db_ready_timeout_seconds=2)
    async with running_app(settings) as (_, client):
        started = time.monotonic()
        ready = await client.get("/api/readyz")
        elapsed = time.monotonic() - started
        live = await client.get("/api/healthz")

    assert ready.status_code == 503
    assert ready.json()["error"]["code"] == "not_ready"
    assert elapsed < 5.0
    assert live.status_code == 200


async def test_a_wrong_password_is_not_ready_and_the_body_leaks_nothing(
    db_config: DbConfig,
) -> None:
    async with running_app(db_config.settings(credential="definitely-wrong-password")) as (
        _,
        client,
    ):
        response = await client.get("/api/readyz")
    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Database is not ready"
    assert "definitely-wrong-password" not in response.text
    assert db_config.app_user not in response.text
