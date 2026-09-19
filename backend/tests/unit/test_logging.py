import json
import logging
from collections.abc import Iterator

import pytest

from app.core.context import reset_request_id, set_request_id
from app.core.logging import JsonFormatter, configure_logging


def make_record(message: str = "hello", **extra: object) -> logging.LogRecord:
    return logging.getLogger("test.logger").makeRecord(
        "test.logger", logging.INFO, __file__, 1, message, (), None, extra=extra
    )


def render(record: logging.LogRecord) -> dict[str, object]:
    return json.loads(JsonFormatter().format(record))  # type: ignore[no-any-return]


def test_output_is_json_with_core_fields() -> None:
    payload = render(make_record("hello world"))
    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert str(payload["timestamp"]).endswith("+00:00")  # timezone-aware UTC


def test_message_arguments_are_interpolated() -> None:
    record = logging.getLogger("t").makeRecord("t", logging.INFO, "f", 1, "n=%d", (7,), None)
    assert render(record)["message"] == "n=7"


def test_request_id_comes_from_context() -> None:
    token = set_request_id("req-context-1")
    try:
        assert render(make_record())["request_id"] == "req-context-1"
    finally:
        reset_request_id(token)


def test_request_id_absent_when_no_context() -> None:
    assert "request_id" not in render(make_record())


def test_extra_fields_are_included() -> None:
    payload = render(make_record(method="GET", status_code=200))
    assert payload["method"] == "GET"
    assert payload["status_code"] == 200


def test_explicit_request_id_extra_overrides_context() -> None:
    token = set_request_id("from-context")
    try:
        assert render(make_record(request_id="explicit-id"))["request_id"] == "explicit-id"
    finally:
        reset_request_id(token)


def test_uvicorn_ansi_color_message_is_not_emitted() -> None:
    """Uvicorn attaches a terminal-coloured copy of each message; it is noise in JSON logs."""
    payload = render(make_record("Started server", color_message="\x1b[36mStarted\x1b[0m"))
    assert "color_message" not in payload
    assert "\x1b" not in json.dumps(payload)


def test_unserialisable_extra_is_stringified_not_fatal() -> None:
    payload = render(make_record(thing=object()))
    assert isinstance(payload["thing"], str)


def test_exception_traceback_is_included() -> None:
    try:
        raise ValueError("bad value")
    except ValueError:
        import sys

        record = logging.getLogger("t").makeRecord(
            "t", logging.ERROR, "f", 1, "failed", (), sys.exc_info()
        )
    payload = render(record)
    assert "ValueError: bad value" in str(payload["exception"])


@pytest.fixture
def restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    uvicorn = {n: (logging.getLogger(n).propagate) for n in ("uvicorn", "uvicorn.error")}
    yield
    root.handlers, root.level = handlers, level
    for name, propagate in uvicorn.items():
        logging.getLogger(name).propagate = propagate


@pytest.mark.usefixtures("restore_root_logger")
def test_configure_logging_is_idempotent() -> None:
    configure_logging("INFO")
    configure_logging("INFO")
    owned = [h for h in logging.getLogger().handlers if getattr(h, "_app_json_handler", False)]
    assert len(owned) == 1
    assert isinstance(owned[0].formatter, JsonFormatter)


@pytest.mark.usefixtures("restore_root_logger")
def test_configure_logging_sets_root_level() -> None:
    configure_logging("WARNING")
    assert logging.getLogger().level == logging.WARNING


@pytest.mark.usefixtures("restore_root_logger")
def test_uvicorn_loggers_flow_through_our_handler_and_access_log_is_silenced() -> None:
    configure_logging("INFO")
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        assert lg.propagate is True
        assert lg.handlers == []
    access = logging.getLogger("uvicorn.access")
    assert access.propagate is False
    assert access.handlers == []
