"""The document endpoints refuse an anonymous or forged request WITHOUT the database or the disk.

Same order as the follow endpoints (ADR 013): Origin, then session, then everything else. An
upload that fails either check must not cost a connection, and must not be read or stored.
"""

from typing import NoReturn

import httpx
import pytest
from fastapi import FastAPI

UPLOAD = "/api/v1/stocks/TCS/documents?title=Q2%20results"
PDF = b"%PDF-1.7\nnot really a pdf"


class NoDatabase:
    def __call__(self) -> NoReturn:
        raise AssertionError("the database must not be touched")


class NoDisk:
    async def put(self, key: str, data: bytes) -> None:
        raise AssertionError("nothing may be stored")

    async def get(self, key: str) -> bytes:
        raise AssertionError("nothing may be read")


@pytest.fixture(autouse=True)
def forbid_database_and_disk(app: FastAPI) -> None:
    app.state.session_factory = NoDatabase()
    app.state.blob_store = NoDisk()


def assert_401(response: httpx.Response) -> None:
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["cache-control"] == "no-store"


async def test_uploading_needs_a_session(client: httpx.AsyncClient) -> None:
    response = await client.post(UPLOAD, content=PDF, headers={"Content-Type": "application/pdf"})
    assert_401(response)


@pytest.mark.parametrize(
    "path", ["/api/v1/stocks/TCS/documents", "/api/v1/documents/1"], ids=["list", "one"]
)
async def test_reading_documents_needs_a_session(client: httpx.AsyncClient, path: str) -> None:
    assert_401(await client.get(path))


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://localhost:9999"])
async def test_a_foreign_origin_is_refused_before_the_session_and_the_body(
    client: httpx.AsyncClient, origin: str
) -> None:
    response = await client.post(
        UPLOAD, content=PDF, headers={"Content-Type": "application/pdf", "Origin": origin}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
