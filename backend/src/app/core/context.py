"""Per-request context that follows the code path without being passed through every signature.

A ``ContextVar`` is the right tool: each asyncio task has its own value, so concurrent requests
cannot see each other's IDs, and ``await`` points and threadpool hops (sync endpoints) inherit it.
"""

from contextvars import ContextVar, Token

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(request_id: str) -> Token[str | None]:
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)
