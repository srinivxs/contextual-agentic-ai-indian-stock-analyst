import re

import pytest

from app.api.middleware import resolve_request_id


@pytest.mark.parametrize(
    "inbound",
    ["abcd1234", "req-1234_5678.9", "A" * 64, "0123456789abcdef0123456789abcdef"],
)
def test_valid_inbound_id_is_kept(inbound: str) -> None:
    assert resolve_request_id(inbound) == inbound


@pytest.mark.parametrize(
    "inbound",
    [
        "",
        "short",  # under 8 chars
        "A" * 65,  # over 64 chars
        "has spaces in it",
        "line\r\nbreak-injection",  # header / log injection attempt
        "<script>alert(1)</script>",
        "ünïcödé-id-1234",
    ],
)
def test_invalid_inbound_id_is_replaced(inbound: str) -> None:
    resolved = resolve_request_id(inbound)
    assert resolved != inbound
    assert re.fullmatch(r"[0-9a-f]{32}", resolved)


def test_missing_id_generates_unique_hex_ids() -> None:
    first, second = resolve_request_id(None), resolve_request_id(None)
    assert re.fullmatch(r"[0-9a-f]{32}", first)
    assert first != second
