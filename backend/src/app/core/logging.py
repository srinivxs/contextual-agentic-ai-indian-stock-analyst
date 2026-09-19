"""Structured JSON logging.

One JSON object per line on stdout: what ECS ships to CloudWatch and what a human can ``jq``.
Every line carries the current request ID automatically (see ``context``), so all logs for one
request can be found with a single filter.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.context import get_request_id

# Attributes every LogRecord has. Anything else on a record came from `extra=` and is emitted.
_RESERVED_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {
    "message",
    "asctime",
    "taskName",
}

# Uvicorn adds a terminal-coloured duplicate of each server message as `color_message`.
# It is ANSI escape codes: noise in JSON logs, so it is dropped.
_DROPPED_EXTRAS = frozenset({"color_message"})

_OWNED_HANDLER_FLAG = "_app_json_handler"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = get_request_id()
        if request_id is not None:
            payload["request_id"] = request_id
        # Applied after the context value so an explicit `extra={"request_id": ...}` wins. That
        # is needed where the context has already been reset (see errors.unhandled_exception).
        for key, value in vars(record).items():
            if (
                key not in _RESERVED_RECORD_ATTRS
                and key not in _DROPPED_EXTRAS
                and not key.startswith("_")
            ):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Route all logging through one JSON handler. Safe to call more than once."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _OWNED_HANDLER_FLAG, False)]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _OWNED_HANDLER_FLAG, True)
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn installs its own plain-text handlers. Hand its server logs to ours...
    for name in ("uvicorn", "uvicorn.error"):
        server_logger = logging.getLogger(name)
        server_logger.handlers.clear()
        server_logger.propagate = True
    # ...and drop its access log: RequestContextMiddleware emits a richer one with the request ID.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
