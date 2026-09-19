import httpx


async def test_healthz_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["content-type"] == "application/json"


async def test_healthz_only_allows_get(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/healthz")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert response.headers["allow"] == "GET"


async def test_openapi_spec_is_served_under_api_prefix(client: httpx.AsyncClient) -> None:
    """Docs live under /api so CloudFront (which only forwards /api/*) can reach them later."""
    spec = await client.get("/api/openapi.json")
    assert spec.status_code == 200
    assert "/api/healthz" in spec.json()["paths"]
    assert (await client.get("/openapi.json")).status_code == 404
