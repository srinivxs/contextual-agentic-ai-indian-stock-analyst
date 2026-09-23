"""PKCE (RFC 7636): the verifier we keep and the challenge we show Google.

The authorization code travels through the browser, so it can end up in history, a Referer header
or a proxy log. PKCE makes a leaked code useless to anyone who does not also hold the verifier,
which never leaves our server.
"""

import base64
import hashlib
import re

import pytest

from app.auth.pkce import CODE_VERIFIER_BYTES, code_challenge, generate_code_verifier

# RFC 7636 Appendix B, the canonical worked example. If our implementation disagrees with this,
# it disagrees with every other implementation in the world.
RFC_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
RFC_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"

# RFC 7636 section 4.1: the verifier alphabet, and 43..128 characters.
UNRESERVED = re.compile(r"[A-Za-z0-9._~-]{43,128}")


def test_the_challenge_matches_the_rfc_7636_worked_example() -> None:
    assert code_challenge(RFC_VERIFIER) == RFC_CHALLENGE


def test_the_challenge_is_sha256_base64url_without_padding() -> None:
    verifier = generate_code_verifier()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
    assert code_challenge(verifier) == expected.decode("ascii").rstrip("=")
    assert "=" not in code_challenge(verifier)  # padding is forbidden in the URL form
    assert set(code_challenge(verifier)) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )


def test_a_verifier_is_unguessable_and_within_the_allowed_shape() -> None:
    verifier = generate_code_verifier()
    assert UNRESERVED.fullmatch(verifier)
    assert CODE_VERIFIER_BYTES >= 32  # at least 256 bits of entropy


def test_every_verifier_is_different() -> None:
    assert len({generate_code_verifier() for _ in range(1000)}) == 1000


def test_one_changed_character_changes_the_challenge() -> None:
    """The whole point: a code stolen with challenge X is useless without the exact verifier."""
    verifier = generate_code_verifier()
    altered = verifier[:-1] + ("A" if verifier[-1] != "A" else "B")
    assert code_challenge(altered) != code_challenge(verifier)


def test_the_challenge_is_deterministic() -> None:
    verifier = generate_code_verifier()
    assert code_challenge(verifier) == code_challenge(verifier)


def test_the_challenge_cannot_be_turned_back_into_the_verifier() -> None:
    """It is a hash, not an encoding: Google learns nothing it could replay."""
    verifier = generate_code_verifier()
    challenge = code_challenge(verifier)
    assert verifier not in challenge
    assert challenge != verifier
    with pytest.raises(Exception):  # noqa: B017,PT011 - not valid base64url of the verifier
        assert base64.urlsafe_b64decode(challenge + "==").decode("ascii") == verifier
