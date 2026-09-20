"""The `oauth_login` cookie: the state, nonce and PKCE verifier of ONE login attempt.

It is signed, not secret: the browser carries it, so the only thing that matters is that we can
tell whether we issued it, unchanged, recently. Without it a stranger could hand us a callback URL
carrying their own `state` and sign us into their account.
"""

import base64
import time

import pytest
from app.auth.login_state import (
    InvalidLoginState,
    LoginAttempt,
    new_login_attempt,
    read_login_attempt,
    sign_login_attempt,
)

from tests.helpers import (
    TEST_GOOGLE_CLIENT_SECRET,
    TEST_SESSION_SECRET,
    build_settings,
)

OTHER_SECRET = "a-completely-different-secret-of-good-length"  # noqa: S105

# --- generating an attempt -----------------------------------------------------------------


def test_an_attempt_carries_a_state_a_nonce_and_a_verifier() -> None:
    attempt = new_login_attempt()
    assert attempt.state
    assert attempt.nonce
    assert attempt.code_verifier
    # Three independent secrets: reusing one value for two purposes would defeat both checks.
    assert len({attempt.state, attempt.nonce, attempt.code_verifier}) == 3


def test_state_and_nonce_are_unguessable() -> None:
    attempt = new_login_attempt()
    for value in (attempt.state, attempt.nonce):
        assert len(value) >= 43  # 256 bits rendered as base64url


@pytest.mark.parametrize("field", ["state", "nonce", "code_verifier"])
def test_no_value_is_ever_reused_between_attempts(field: str) -> None:
    values = {getattr(new_login_attempt(), field) for _ in range(500)}
    assert len(values) == 500


# --- signing and reading back --------------------------------------------------------------


def test_a_freshly_signed_attempt_reads_back_unchanged() -> None:
    settings = build_settings()
    attempt = new_login_attempt()
    assert read_login_attempt(sign_login_attempt(attempt, settings), settings) == attempt


def test_the_signed_value_is_safe_to_put_in_a_cookie() -> None:
    signed = sign_login_attempt(new_login_attempt(), build_settings())
    assert signed.isascii()
    for forbidden in (";", ",", " ", '"', "\n", "\r"):
        assert forbidden not in signed


def test_the_cookie_never_carries_any_secret_of_ours() -> None:
    """Signed, NOT encrypted: anyone holding the cookie can decode the payload.

    That is acceptable only because the payload holds nothing but this one attempt's single-use
    values. Our session secret and Google client secret must never be in there, in any encoding.
    """
    signed = sign_login_attempt(new_login_attempt(), build_settings())
    decoded = " ".join(
        base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)).decode("utf-8", "replace")
        for part in signed.split(".")
    )
    for secret in (TEST_SESSION_SECRET, TEST_GOOGLE_CLIENT_SECRET):
        assert secret not in signed
        assert secret not in decoded


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda s: s[:-1],
        lambda s: s + "x",
        lambda s: s[1:],
        lambda s: s.replace(".", "x", 1),
        lambda s: s.swapcase(),
        lambda s: "",
        lambda s: "not-a-signed-value",
    ],
    ids=["truncated", "appended", "head-removed", "separator", "case", "empty", "nonsense"],
)
def test_a_tampered_value_is_refused(corrupt: object) -> None:
    settings = build_settings()
    signed = sign_login_attempt(new_login_attempt(), settings)
    with pytest.raises(InvalidLoginState):
        read_login_attempt(corrupt(signed), settings)  # type: ignore[operator]


def test_a_forged_payload_with_an_attackers_own_state_is_refused() -> None:
    """The attack the signature exists to stop: a cookie we never issued."""
    settings = build_settings()
    forged = LoginAttempt(state="attacker-state", nonce="attacker-nonce", code_verifier="x" * 43)
    payload = base64.urlsafe_b64encode(
        f'{{"state":"{forged.state}","nonce":"{forged.nonce}",'
        f'"code_verifier":"{forged.code_verifier}"}}'.encode()
    ).decode()
    with pytest.raises(InvalidLoginState):
        read_login_attempt(f"{payload}.forged.signature", settings)


def test_a_value_signed_with_another_secret_is_refused() -> None:
    """Rotating SESSION_SECRET invalidates logins in flight, and nothing else."""
    signed = sign_login_attempt(new_login_attempt(), build_settings())
    other = build_settings(session_secret=OTHER_SECRET)
    with pytest.raises(InvalidLoginState):
        read_login_attempt(signed, other)


def test_a_value_older_than_the_configured_ttl_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A login tab left open for hours must not still be completable.

    The clock is frozen and then moved forward, so the test needs no real waiting. This assumes the
    signer stamps the value using ``time.time()``.
    """
    settings = build_settings(oauth_login_ttl_seconds=600)
    clock = {"now": 1_800_000_000.0}
    monkeypatch.setattr(time, "time", lambda: clock["now"])
    signed = sign_login_attempt(new_login_attempt(), settings)

    clock["now"] += 599  # still inside the ten-minute window
    assert read_login_attempt(signed, settings).state

    clock["now"] += 2  # 601 seconds old
    with pytest.raises(InvalidLoginState):
        read_login_attempt(signed, settings)


def test_the_ttl_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 1_800_000_000.0}
    monkeypatch.setattr(time, "time", lambda: clock["now"])
    short, long = (
        build_settings(oauth_login_ttl_seconds=60),
        build_settings(oauth_login_ttl_seconds=1800),
    )
    signed = sign_login_attempt(new_login_attempt(), short)

    clock["now"] += 120
    with pytest.raises(InvalidLoginState):
        read_login_attempt(signed, short)
    assert read_login_attempt(signed, long).state  # same value, more generous reader


def test_two_attempts_produce_different_cookies_that_cannot_be_swapped() -> None:
    settings = build_settings()
    first, second = new_login_attempt(), new_login_attempt()
    signed_first, signed_second = (
        sign_login_attempt(first, settings),
        sign_login_attempt(second, settings),
    )
    assert signed_first != signed_second
    assert read_login_attempt(signed_first, settings) == first
    assert read_login_attempt(signed_second, settings) == second


def test_the_failure_message_is_generic_and_echoes_nothing() -> None:
    """Whatever went wrong, the reason must not reach the browser or a log line verbatim."""
    settings = build_settings()
    signed = sign_login_attempt(new_login_attempt(), settings)
    with pytest.raises(InvalidLoginState) as caught:
        read_login_attempt(signed + "x", settings)
    assert str(caught.value) == "invalid login state"
    assert signed not in str(caught.value)
