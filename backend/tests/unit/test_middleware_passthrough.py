from starlette.types import Message, Receive, Scope, Send

from app.api.middleware import RequestContextMiddleware


async def test_non_http_scopes_pass_through_untouched() -> None:
    """Lifespan/websocket traffic must not get request IDs, timing, or logging."""
    seen: list[Scope] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope)

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:  # pragma: no cover - never called
        raise AssertionError("middleware should not send for lifespan scopes")

    scope: Scope = {"type": "lifespan"}
    await RequestContextMiddleware(inner)(scope, receive, send)

    assert seen == [scope]
    assert "state" not in scope
