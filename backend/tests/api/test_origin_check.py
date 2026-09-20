"""`require_same_origin`: the CSRF guard for every state-changing route, now and in P5+."""

from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.auth.deps import require_same_origin
from app.main import create_app
from tests.helpers import build_settings, production_settings


async def echo() -> dict[str, str]:
    return {"ok": "yes"}


def guarded_app(*, production: bool = False, **overrides: Any) -> FastAPI:
    settings = production_settings(**overrides) if production else build_settings(**overrides)
    app = create_app(settings)
    app.add_api_route(
        "/_test/guarded",
        echo,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        dependencies=[Depends(require_same_origin)],
    )
    return app


def client_for(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_every_state_changing_method_rejects_a_foreign_origin(method: str) -> None:
    async with client_for(guarded_app()) as client:
        response = await client.request(
            method, "/_test/guarded", headers={"Origin": "https://x.test"}
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert response.json()["error"]["message"] == "Cross-origin request rejected"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_every_state_changing_method_accepts_our_origin_and_no_origin(method: str) -> None:
    async with client_for(guarded_app()) as client:
        same = await client.request(
            method, "/_test/guarded", headers={"Origin": "http://localhost:8000"}
        )
        none = await client.request(method, "/_test/guarded")
    assert (same.status_code, none.status_code) == (200, 200)


async def test_safe_methods_are_never_checked() -> None:
    async with client_for(guarded_app()) as client:
        response = await client.get("/_test/guarded", headers={"Origin": "https://x.test"})
    assert response.status_code == 200


async def test_a_path_in_the_configured_base_url_does_not_change_the_allowed_origin() -> None:
    app = guarded_app(public_base_url="http://localhost:8000/some/path/")
    async with client_for(app) as client:
        ok = await client.post("/_test/guarded", headers={"Origin": "http://localhost:8000"})
        bad = await client.post("/_test/guarded", headers={"Origin": "http://localhost:8000/some"})
    assert (ok.status_code, bad.status_code) == (200, 403)


async def test_in_production_only_the_https_origin_is_accepted() -> None:
    async with client_for(guarded_app(production=True)) as client:
        https = await client.post("/_test/guarded", headers={"Origin": "https://app.example.test"})
        http = await client.post("/_test/guarded", headers={"Origin": "http://app.example.test"})
    assert (https.status_code, http.status_code) == (200, 403)
