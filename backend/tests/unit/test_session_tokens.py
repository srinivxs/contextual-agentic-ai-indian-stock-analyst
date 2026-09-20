"""Session tokens: what the browser holds, and what the database holds instead."""

import hashlib
import re
import secrets

import pytest

from app.auth.sessions import generate_session_token, hash_session_token


def test_a_token_carries_256_random_bits_as_url_safe_text() -> None:
    value = generate_session_token()
    # 32 bytes -> 43 base64url characters (no padding): safe in a cookie, nothing to escape.
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", value)


def test_tokens_come_from_the_operating_systems_secure_random_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guessable tokens would let anyone become anyone: only `secrets` is acceptable, 32 bytes."""
    calls: list[int] = []

    def fake_token_urlsafe(nbytes: int) -> str:
        calls.append(nbytes)
        return "x" * 43

    monkeypatch.setattr(secrets, "token_urlsafe", fake_token_urlsafe)
    assert generate_session_token() == "x" * 43
    assert calls == [32]


def test_two_thousand_tokens_are_all_different() -> None:
    assert len({generate_session_token() for _ in range(2000)}) == 2000


def test_the_stored_form_is_the_sha256_digest_of_the_token() -> None:
    value = generate_session_token()
    digest = hash_session_token(value)
    assert isinstance(digest, bytes)
    assert digest == hashlib.sha256(value.encode("ascii")).digest()
    assert len(digest) == 32


def test_hashing_is_deterministic_and_sensitive_to_every_character() -> None:
    value = generate_session_token()
    assert hash_session_token(value) == hash_session_token(value)
    flipped = value[:-1] + ("A" if value[-1] != "A" else "B")
    assert hash_session_token(flipped) != hash_session_token(value)


def test_the_stored_form_reveals_nothing_of_the_token() -> None:
    """A database leak must not hand out working cookies."""
    value = generate_session_token()
    digest = hash_session_token(value)
    assert value.encode("ascii") not in digest
    assert digest.hex() != value
