"""`Cache-Control: no-store` belongs on authentication and session responses only.

It must NOT become a blanket policy: unrelated current and future endpoints stay cacheable unless
they opt in themselves.
"""

import httpx
import pytest
from fastapi import FastAPI

from app.api.middleware import is_no_store_path


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/me",
        "/api/v1/auth/logout",
        "/api/v1/auth/google/login",  # P4b
        "/api/v1/auth/google/callback",  # P4b
        "/api/v1/auth/anything-else",
    ],
)
def test_authentication_paths_are_no_store(path: str) -> None:
    assert is_no_store_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "/api/healthz",
        "/api/readyz",
        "/api/docs",
        "/api/v1/stocks",  # a future unrelated endpoint
        "/api/v1/me-not-really",  # a prefix look-alike must not match
        "/api/v1/authors",  # neither must a sibling that merely starts with "auth"
        "/",
    ],
)
def test_other_paths_are_left_alone(path: str) -> None:
    assert not is_no_store_path(path)


async def test_the_header_is_present_on_auth_responses_of_every_status(
    client: httpx.AsyncClient,
) -> None:
    responses = [
        await client.get("/api/v1/me"),  # 401 (no cookie, so no database is touched)
        await client.post("/api/v1/auth/logout"),  # 204
        await client.get("/api/v1/auth/logout"),  # 405
        await client.post("/api/v1/auth/logout", headers={"Origin": "https://evil.example"}),  # 403
        await client.get("/api/v1/auth/no-such-thing"),  # 404 under the auth prefix
    ]
    assert [r.status_code for r in responses] == [401, 204, 405, 403, 404]
    assert all(r.headers["cache-control"] == "no-store" for r in responses)


async def test_unrelated_responses_get_no_cache_header_from_us(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    assert "cache-control" not in (await client.get("/api/healthz")).headers
    assert "cache-control" not in (await client.get("/api/v1/stocks")).headers  # 404 today
    assert "cache-control" not in (await client.get("/api/docs")).headers
