"""One error shape for the whole API.

Every non-2xx response has the body::

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": [...]}}

``details`` appears only for validation errors. Domain code raises ``AppError``; framework errors
(404, 405, 422) and unexpected crashes are mapped here so clients only ever parse one format.
"""

import logging
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.errors")

REQUEST_ID_HEADER = "X-Request-ID"

_HTTP_ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
}


class AppError(Exception):
    """An error the application raises on purpose, with the HTTP status and code to report."""

    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _request_id(request: Request) -> str | None:
    # Read from request.state, not the ContextVar: Starlette's outermost error middleware renders
    # unhandled-exception responses *outside* our middleware, after the ContextVar was reset.
    return cast("str | None", getattr(request.state, "request_id", None))


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message, "request_id": _request_id(request)}
    if details is not None:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status_code, headers=headers)


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    error = cast(AppError, exc)
    return _error_response(
        request, status_code=error.status_code, code=error.code, message=error.message
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    error = cast(StarletteHTTPException, exc)
    return _error_response(
        request,
        status_code=error.status_code,
        code=_HTTP_ERROR_CODES.get(error.status_code, "http_error"),
        message=str(error.detail),
        headers=dict(error.headers) if error.headers else None,  # e.g. Allow on a 405
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    error = cast(RequestValidationError, exc)
    # Only where/what/why. Pydantic's `input` (the caller's raw value) is deliberately dropped.
    details = [
        {"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]}
        for item in error.errors()
    ]
    return _error_response(
        request,
        status_code=422,
        code="validation_error",
        message="Request validation failed",
        details=details,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = _request_id(request)
    logger.error(
        "unhandled_exception",
        exc_info=exc,
        # Explicit: the ContextVar is already reset by the time this runs.
        extra={"request_id": request_id, "method": request.method, "path": request.url.path},
    )
    # Generic message: never leak exception text. This response bypasses RequestContextMiddleware,
    # so it must set the request-ID header itself.
    headers = {REQUEST_ID_HEADER: request_id} if request_id else None
    return _error_response(
        request,
        status_code=500,
        code="internal_error",
        message="Internal server error",
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
