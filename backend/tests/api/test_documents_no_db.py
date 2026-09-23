"""The document endpoints refuse an anonymous request WITHOUT the database, and nobody can upload.

Documents enter only through the worker (ADR 018; uploads removed in P9d), so a POST to the list
is refused as a method the route does not have, before any session or database work.
"""

from typing import NoReturn

import httpx
import pytest
from fastapi import FastAPI


class NoDatabase:
    def __call__(self) -> NoReturn:
        raise AssertionError("the database must not be touched")


@pytest.fixture(autouse=True)
def forbid_database(app: FastAPI) -> None:
    app.state.session_factory = NoDatabase()


@pytest.mark.parametrize(
    "path", ["/api/v1/stocks/TCS/documents", "/api/v1/documents/1"], ids=["list", "one"]
)
async def test_reading_documents_needs_a_session(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "path", ["/api/v1/stocks/TCS/documents", "/api/v1/documents/1"], ids=["list", "one"]
)
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
async def test_there_is_no_way_to_add_change_or_remove_a_document(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    response = await client.request(
        method, path, content=b"%PDF-1.7 demo", headers={"Content-Type": "application/pdf"}
    )

    assert response.status_code == 405
    assert "error" in response.json()
