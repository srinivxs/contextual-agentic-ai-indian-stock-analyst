"""Request context middleware: request ID, timing, and the access log.

Written as a *pure ASGI* middleware rather than ``BaseHTTPMiddleware``: the latter runs the app in
a separate task, which breaks ContextVar propagation and complicates streaming and exceptions.
"""

import logging
import re
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import reset_request_id, set_request_id
from app.core.errors import REQUEST_ID_HEADER

logger = logging.getLogger("app.request")

# Client-supplied IDs are accepted (useful for tracing across proxies) but only if they are safe
# to put in a response header and a log line: no whitespace, control characters, or odd Unicode.
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")

# Polled constantly by the load balancer; logged at DEBUG so they do not flood the logs.
_QUIET_PATHS = frozenset({"/api/healthz"})


def resolve_request_id(inbound: str | None) -> str:
    """Keep a well-formed inbound ID, otherwise mint a new one."""
    if inbound is not None and _VALID_REQUEST_ID.fullmatch(inbound):
        return inbound
    return uuid.uuid4().hex


# Responses that depend on who is asking, or that set or clear a session cookie, must never be
# stored by a browser or a shared cache. Deliberately NOT applied to all of /api/v1: unrelated
# future endpoints stay cacheable unless they opt in themselves.
# `/api/v1/stocks` carries the caller's own follows. The prefixes end in a slash, so look-alikes
# such as `/api/v1/stocks-archive` or `/api/v1/authors` are not caught.
_NO_STORE_EXACT = frozenset({"/api/v1/me", "/api/v1/stocks"})
# `/api/v1/documents/` (P9) needs a session, and a document's status changes as it is ingested.
_NO_STORE_PREFIXES = ("/api/v1/auth/", "/api/v1/stocks/", "/api/v1/documents/")


def is_no_store_path(path: str) -> bool:
    return path in _NO_STORE_EXACT or path.startswith(_NO_STORE_PREFIXES)


class NoStoreMiddleware:
    """Adds ``Cache-Control: no-store`` to authentication, session and per-user responses.

    A middleware (not a per-route header) so it also covers the responses the framework builds by
    itself: a 401 or 403 error envelope, or a 405 for the wrong method.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_no_store_path(scope["path"]):
            await self.app(scope, receive, send)
            return

        async def send_with_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_no_store)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # lifespan and websocket traffic pass straight through
            await self.app(scope, receive, send)
            return

        request_id = resolve_request_id(Headers(scope=scope).get(REQUEST_ID_HEADER))
        # Two homes for the ID: request.state (for error handlers) and the ContextVar (for logs).
        scope.setdefault("state", {})["request_id"] = request_id
        token = set_request_id(request_id)

        started = time.perf_counter()
        status_code = 500  # if the app raises before responding, the outer layer sends a 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", [])
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            level = logging.DEBUG if scope["path"] in _QUIET_PATHS else logging.INFO
            logger.log(
                level,
                "request_completed",
                extra={
                    "method": scope["method"],
                    # Path only. The query string is never logged: it will carry OAuth codes.
                    "path": scope["path"],
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            reset_request_id(token)
