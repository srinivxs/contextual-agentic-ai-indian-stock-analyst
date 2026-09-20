"""`GET /api/v1/me` and `POST /api/v1/auth/logout` over HTTP, against the real database."""

import asyncio
import io
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.auth.sessions import hash_session_token
from app.core.logging import JsonFormatter
from tests.helpers import parse_set_cookie, production_settings, running_app
from tests.integration.auth_helpers import age_all_sessions, count_sessions, open_session
from tests.integration.conftest import DbConfig, MakeUser

Factory = async_sessionmaker[AsyncSession]


def cookie(name: str, value: str) -> dict[str, str]:
    return {"Cookie": f"{name}={value}"}


@contextmanager
def captured_logs() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield stream
    finally:
        root.removeHandler(handler)


async def test_me_returns_exactly_the_id_and_email_of_the_signed_in_user(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    user_id = await make_user(email="ada@example.test", sub="google-sub-that-must-not-leak")
    value = await open_session(session_factory, user_id)

    async with running_app(db_config.settings()) as (_, client):
        response = await client.get("/api/v1/me", headers=cookie("session", value))

    assert response.status_code == 200
    assert response.json() == {"id": str(user_id), "email": "ada@example.test"}
    assert response.headers["cache-control"] == "no-store"
    for secret in (value, hash_session_token(value).hex(), "google-sub-that-must-not-leak"):
        assert secret not in response.text  # nothing internal is ever echoed back


@pytest.mark.parametrize(
    "kind", ["unknown", "garbage", "very-long", "tampered", "expired", "deleted-user-session"]
)
async def test_me_is_401_for_every_kind_of_bad_cookie(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    kind: str,
) -> None:
    user_id = await make_user()
    value = await open_session(session_factory, user_id)
    presented = {
        "unknown": "A" * 43,
        "garbage": "%%%;;;éé ' OR 1=1 --",
        "very-long": "z" * 10_000,
        "tampered": value[:-1] + ("A" if value[-1] != "A" else "B"),
        "expired": value,
        "deleted-user-session": value,
    }[kind]
    if kind == "expired":
        await age_all_sessions(admin_engine)
    if kind == "deleted-user-session":
        async with admin_engine.begin() as connection:
            await connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})

    async with running_app(db_config.settings()) as (_, client):
        # Raw bytes, as a browser may send them: httpx would refuse a non-ASCII str header value.
        raw = {b"Cookie": f"session={presented}".encode("latin-1")}
        response = await client.get("/api/v1/me", headers=raw)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["cache-control"] == "no-store"


async def test_me_does_not_extend_the_session(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """The lifetime is fixed: using the session never renews it."""
    value = await open_session(session_factory, await make_user())

    async def expiry() -> datetime:
        async with admin_engine.connect() as connection:
            expires: datetime = (
                await connection.execute(text("SELECT expires_at FROM sessions"))
            ).scalar_one()
            return expires

    before = await expiry()
    async with running_app(db_config.settings()) as (_, client):
        for _ in range(3):
            assert (
                await client.get("/api/v1/me", headers=cookie("session", value))
            ).status_code == 200
    assert await expiry() == before


async def test_logout_ends_the_session_and_clears_the_cookie(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    value = await open_session(session_factory, await make_user())

    async with running_app(db_config.settings()) as (_, client):
        assert (await client.get("/api/v1/me", headers=cookie("session", value))).status_code == 200
        response = await client.post("/api/v1/auth/logout", headers=cookie("session", value))
        after = await client.get("/api/v1/me", headers=cookie("session", value))

    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    name, _, attributes = parse_set_cookie(response.headers["set-cookie"])
    assert (name, attributes["max-age"]) == ("session", "0")
    assert await count_sessions(admin_engine) == 0  # deleted, not merely marked
    assert after.status_code == 401  # the old cookie is now worthless


async def test_logging_out_twice_is_fine(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    value = await open_session(session_factory, await make_user())
    async with running_app(db_config.settings()) as (_, client):
        first = await client.post("/api/v1/auth/logout", headers=cookie("session", value))
        second = await client.post("/api/v1/auth/logout", headers=cookie("session", value))
    assert (first.status_code, second.status_code) == (204, 204)


async def test_logout_ends_only_the_session_that_asked(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    user_id = await make_user()
    laptop = await open_session(session_factory, user_id)
    phone = await open_session(session_factory, user_id)

    async with running_app(db_config.settings()) as (_, client):
        await client.post("/api/v1/auth/logout", headers=cookie("session", laptop))
        laptop_after = await client.get("/api/v1/me", headers=cookie("session", laptop))
        phone_after = await client.get("/api/v1/me", headers=cookie("session", phone))

    assert (laptop_after.status_code, phone_after.status_code) == (401, 200)


async def test_simultaneous_logouts_all_succeed(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    value = await open_session(session_factory, await make_user())
    async with running_app(db_config.settings()) as (_, client):
        responses = await asyncio.gather(
            *(
                client.post("/api/v1/auth/logout", headers=cookie("session", value))
                for _ in range(8)
            )
        )
    assert {r.status_code for r in responses} == {204}
    assert await count_sessions(admin_engine) == 0


async def test_a_foreign_origin_cannot_log_someone_out(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """The CSRF guard, end to end: the forged request is refused and the session survives."""
    value = await open_session(session_factory, await make_user())
    headers = {**cookie("session", value), "Origin": "https://evil.example"}

    async with running_app(db_config.settings()) as (_, client):
        response = await client.post("/api/v1/auth/logout", headers=headers)
        still_in = await client.get("/api/v1/me", headers=cookie("session", value))

    assert response.status_code == 403
    assert "set-cookie" not in response.headers
    assert still_in.status_code == 200
    assert await count_sessions(admin_engine) == 1


async def test_development_reads_only_the_plain_cookie_name(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    value = await open_session(session_factory, await make_user())
    async with running_app(db_config.settings()) as (_, client):
        plain = await client.get("/api/v1/me", headers=cookie("session", value))
        prefixed = await client.get("/api/v1/me", headers=cookie("__Host-session", value))
    assert (plain.status_code, prefixed.status_code) == (200, 401)


async def test_production_reads_only_the_host_prefixed_cookie_name(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    """A plain `session` cookie is not accepted in production, even with a valid token in it."""
    value = await open_session(session_factory, await make_user())
    settings = production_settings(
        database_url=db_config.settings().database_url.get_secret_value()
    )

    async with running_app(settings) as (_, client):
        prefixed = await client.get("/api/v1/me", headers=cookie("__Host-session", value))
        plain = await client.get("/api/v1/me", headers=cookie("session", value))
        logout = await client.post("/api/v1/auth/logout", headers=cookie("__Host-session", value))

    assert (prefixed.status_code, plain.status_code) == (200, 401)
    name, _, attributes = parse_set_cookie(logout.headers["set-cookie"])
    assert name == "__Host-session"
    assert {"secure", "httponly"} <= set(attributes)
    assert "domain" not in attributes


async def test_no_log_line_ever_contains_the_session_token_or_its_hash(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    value = await open_session(session_factory, await make_user())
    bad = "canary-cookie-value-that-must-not-be-logged"

    with captured_logs() as stream:
        async with running_app(db_config.settings()) as (_, client):
            await client.get("/api/v1/me", headers=cookie("session", value))
            await client.get("/api/v1/me", headers=cookie("session", bad))
            await client.post("/api/v1/auth/logout", headers=cookie("session", value))

    logs = stream.getvalue()
    assert "/api/v1/me" in logs  # the requests really were logged...
    for secret in (value, bad, hash_session_token(value).hex(), "session="):
        assert secret not in logs  # ...and none of them carries a cookie or a token
