"""Verifying Google's ID token: the signature first, then every claim we depend on.

The ID token is the only thing that tells us who just signed in. It arrives over TLS straight from
Google's token endpoint, but we verify it cryptographically anyway, so that a bug elsewhere (a
mixed-up response, a proxy, a future refactor that moves the token somewhere less trusted) cannot
turn into an account takeover. **There is no path through this module that returns an identity
without a valid RS256 signature from a key Google published.**
"""

import base64
import io
import json
import logging
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from app.auth.id_token import (
    CLOCK_SKEW_LEEWAY_SECONDS,
    GoogleIdentity,
    InvalidIdToken,
    verify_id_token,
)
from app.auth.jwks import JwksCache
from app.core.logging import JsonFormatter
from tests.google_fakes import (
    CURRENT_JWKS,
    EMAIL,
    KID,
    ROTATED_JWKS,
    SIGNING_KEY,
    SUB,
    WRONG_KEY,
    WRONG_KID,
    FakeJwksEndpoint,
    mint_hmac_forged_token,
    mint_id_token,
    mint_unsigned_token,
)
from tests.helpers import TEST_GOOGLE_CLIENT_ID, build_settings

NONCE = "the-expected-nonce"


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def verify(
    token: str, *, google: FakeJwksEndpoint | None = None, **kwargs: Any
) -> GoogleIdentity:
    """Verify `token` against a fake Google, with the expected nonce unless overridden."""
    endpoint = google if google is not None else FakeJwksEndpoint()
    async with endpoint.client() as client:
        cache = JwksCache(client=client, clock=Clock())
        return await verify_id_token(
            token,
            jwks=cache,
            settings=build_settings(),
            nonce=kwargs.pop("nonce", NONCE),
            **kwargs,
        )


# --- the happy path ---------------------------------------------------------------------------


async def test_a_genuinely_signed_token_yields_the_identity() -> None:
    identity = await verify(mint_id_token())
    assert identity == GoogleIdentity(google_sub=SUB, email=EMAIL)


async def test_the_identity_is_the_google_sub_not_the_email() -> None:
    """A user may change their Google email; `sub` never changes and is never reused."""
    identity = await verify(mint_id_token(email="changed@example.test"))
    assert identity.google_sub == SUB
    assert identity.email == "changed@example.test"


async def test_both_issuer_spellings_google_uses_are_accepted() -> None:
    for issuer in ("https://accounts.google.com", "accounts.google.com"):
        assert await verify(mint_id_token(issuer=issuer))


async def test_nothing_google_sends_beyond_our_needs_is_returned() -> None:
    """`profile`-ish claims may appear; we must not start carrying them around."""
    identity = await verify(
        mint_id_token(extra={"name": "Ada L", "picture": "https://x.test/a.png", "hd": "x.test"})
    )
    assert identity == GoogleIdentity(google_sub=SUB, email=EMAIL)


# --- the signature ----------------------------------------------------------------------------


async def test_a_token_signed_by_the_wrong_key_is_refused() -> None:
    """Every claim is perfect; only the signature is not Google's. That alone must be fatal."""
    with pytest.raises(InvalidIdToken):
        await verify(mint_id_token(key=WRONG_KEY))


async def test_a_token_signed_by_a_key_google_publishes_under_a_different_kid_is_refused() -> None:
    google = FakeJwksEndpoint(ROTATED_JWKS)
    with pytest.raises(InvalidIdToken):
        await verify(mint_id_token(key=WRONG_KEY, kid=KID), google=google)


async def test_an_unsigned_token_is_refused() -> None:
    """`alg: none` is the oldest JWT forgery there is."""
    with pytest.raises(InvalidIdToken):
        await verify(mint_unsigned_token())


async def test_a_token_signed_with_the_public_key_as_an_hmac_secret_is_refused() -> None:
    """The RS256-to-HS256 confusion attack.

    Google's verification key is public by definition. If the token's own header could choose the
    algorithm, an attacker could sign with HS256 using that public key as the shared secret and be
    believed. Only RS256 may ever be accepted.
    """
    now = time.time()
    forged = mint_hmac_forged_token(
        {
            "iss": "https://accounts.google.com",
            "aud": TEST_GOOGLE_CLIENT_ID,
            "sub": SUB,
            "email": EMAIL,
            "email_verified": True,
            "nonce": NONCE,
            "iat": now,
            "exp": now + 3600,
        }
    )
    with pytest.raises(InvalidIdToken):
        await verify(forged)


async def test_a_tampered_payload_is_refused() -> None:
    """Change one claim after signing and the signature no longer matches."""
    header, payload, signature = mint_id_token().split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    claims["sub"] = "somebody-else"
    swapped = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    with pytest.raises(InvalidIdToken):
        await verify(f"{header}.{swapped}.{signature}")


@pytest.mark.parametrize(
    "token",
    ["", "not-a-jwt", "a.b", "a.b.c.d", "...", "eyJhbGciOiJSUzI1NiJ9..", "%%%.%%%.%%%"],
    ids=["empty", "no-dots", "two-parts", "four-parts", "dots-only", "no-signature", "not-base64"],
)
async def test_a_malformed_token_is_refused(token: str) -> None:
    with pytest.raises(InvalidIdToken):
        await verify(token)


async def test_a_token_with_no_kid_in_its_header_is_refused() -> None:
    """Without a `kid` we cannot know which key to check, and must not try them all."""
    token = jwt.encode({"sub": SUB}, SIGNING_KEY, algorithm="RS256")  # no kid header
    with pytest.raises(InvalidIdToken):
        await verify(token)


# --- the claims -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "issuer",
    ["https://evil.example", "https://accounts.google.com.evil.example", "", "google.com"],
)
async def test_a_token_from_another_issuer_is_refused(issuer: str) -> None:
    with pytest.raises(InvalidIdToken):
        await verify(mint_id_token(issuer=issuer))


async def test_a_token_minted_for_another_application_is_refused() -> None:
    """The important one: a real, valid Google token for a DIFFERENT app must not log anyone in."""
    with pytest.raises(InvalidIdToken):
        await verify(mint_id_token(audience="999-someone-elses-app.apps.googleusercontent.com"))


async def test_an_expired_token_is_refused() -> None:
    now = time.time()
    with pytest.raises(InvalidIdToken):
        await verify(mint_id_token(now=now, expires_at=now - 120))


async def test_expiry_allows_a_minute_of_clock_skew() -> None:
    """Our host's clock and Google's will never agree exactly."""
    now = time.time()
    assert await verify(mint_id_token(now=now, expires_at=now - 30))
    assert CLOCK_SKEW_LEEWAY_SECONDS == 60


async def test_a_token_with_no_expiry_is_refused() -> None:
    with pytest.raises(InvalidIdToken):
        await verify(mint_id_token(drop=("exp",)))


@pytest.mark.parametrize(
    "nonce", ["a-different-nonce", "", "the-expected-nonc"], ids=["different", "empty", "near-miss"]
)
async def test_a_token_whose_nonce_is_not_the_one_we_sent_is_refused(nonce: str) -> None:
    """This is what ties the token to THIS login attempt; a replayed one carries another nonce."""
    with pytest.raises(InvalidIdToken):
        await verify(mint_id_token(nonce=nonce))


async def test_a_token_with_no_nonce_at_all_is_refused() -> None:
    with pytest.raises(InvalidIdToken):
        await verify(mint_id_token(drop=("nonce",)))


@pytest.mark.parametrize("verified", [False, None], ids=["false", "missing"])
async def test_an_unverified_email_is_refused(verified: bool | None) -> None:
    """Anyone can claim an address they have not proved they own."""
    drop = ("email_verified",) if verified is None else ()
    with pytest.raises(InvalidIdToken):
        await verify(mint_id_token(email_verified=verified, drop=drop))


@pytest.mark.parametrize("subject", [None, "", "   "], ids=["missing", "empty", "blank"])
async def test_a_token_without_a_usable_subject_is_refused(subject: str | None) -> None:
    drop = ("sub",) if subject is None else ()
    with pytest.raises(InvalidIdToken):
        await verify(mint_id_token(subject=subject, drop=drop))


@pytest.mark.parametrize("email", [None, "", "   "], ids=["missing", "empty", "blank"])
async def test_a_token_without_an_email_is_refused(email: str | None) -> None:
    """We store the address, so a token that omits it cannot produce a user row."""
    drop = ("email",) if email is None else ()
    with pytest.raises(InvalidIdToken):
        await verify(mint_id_token(email=email, drop=drop))


# --- `iat`: the approved rule ------------------------------------------------------------------


async def test_a_token_issued_far_in_the_future_is_refused() -> None:
    now = time.time()
    with pytest.raises(InvalidIdToken):
        await verify(mint_id_token(now=now, issued_at=now + 600))


async def test_a_token_issued_slightly_in_the_future_is_accepted() -> None:
    """Within the clock-skew allowance, a future `iat` is just two clocks disagreeing."""
    now = time.time()
    assert await verify(mint_id_token(now=now, issued_at=now + 30))


async def test_an_old_but_unexpired_token_is_NOT_refused_for_being_old() -> None:
    """Deliberate: `exp` bounds the lifetime and `nonce` binds it to this login. No maximum age."""
    now = time.time()
    assert await verify(mint_id_token(now=now, issued_at=now - 86400, expires_at=now + 600))


async def test_a_token_with_no_iat_at_all_is_accepted() -> None:
    """`iat` is optional for us: it is checked only if present."""
    assert await verify(mint_id_token(drop=("iat",)))


# --- where the key comes from (the boundary the owner called out) -----------------------------


async def test_verification_succeeds_when_google_is_down_but_the_cached_key_fits() -> None:
    google = FakeJwksEndpoint(headers={"Cache-Control": "max-age=3600"})
    clock = Clock()
    async with google.client() as client:
        cache = JwksCache(client=client, clock=clock)
        settings = build_settings()
        assert await verify_id_token(mint_id_token(), jwks=cache, settings=settings, nonce=NONCE)
        google.go_down()
        clock.advance(3700)  # stale, but inside the grace period
        assert await verify_id_token(mint_id_token(), jwks=cache, settings=settings, nonce=NONCE)


async def test_verification_fails_when_google_is_down_and_we_hold_no_key() -> None:
    """**No key, no verification, no login.** The signature check is never skipped."""
    google = FakeJwksEndpoint()
    google.go_down()
    with pytest.raises(InvalidIdToken):
        await verify(mint_id_token(), google=google)


async def test_a_rotated_key_is_picked_up_by_the_forced_refresh() -> None:
    google = FakeJwksEndpoint(CURRENT_JWKS, headers={"Cache-Control": "max-age=3600"})
    clock = Clock()
    async with google.client() as client:
        cache = JwksCache(client=client, clock=clock)
        settings = build_settings()
        await verify_id_token(mint_id_token(), jwks=cache, settings=settings, nonce=NONCE)
        google.document = ROTATED_JWKS  # Google now also signs with WRONG_KEY under WRONG_KID
        token = mint_id_token(key=WRONG_KEY, kid=WRONG_KID)
        assert await verify_id_token(token, jwks=cache, settings=settings, nonce=NONCE)
    assert google.calls == 2


async def test_a_token_naming_a_key_google_does_not_have_is_refused() -> None:
    with pytest.raises(InvalidIdToken):
        await verify(mint_id_token(kid="a-key-that-does-not-exist"))


# --- what the failure tells the world ----------------------------------------------------------


@pytest.mark.parametrize(
    "token_factory",
    [
        lambda: mint_id_token(key=WRONG_KEY),
        lambda: mint_id_token(issuer="https://evil.example"),
        lambda: mint_id_token(nonce="wrong"),
        lambda: mint_unsigned_token(),
    ],
    ids=["bad-signature", "bad-issuer", "bad-nonce", "unsigned"],
)
async def test_every_failure_raises_the_same_opaque_error(token_factory: Any) -> None:
    """One message for every cause: the browser must not learn which check it tripped."""
    with pytest.raises(InvalidIdToken) as caught:
        await verify(token_factory())
    assert str(caught.value) == "invalid id token"


async def test_the_error_never_carries_the_token_or_its_claims() -> None:
    token = mint_id_token(key=WRONG_KEY)
    with pytest.raises(InvalidIdToken) as caught:
        await verify(token)
    message = str(caught.value)
    for secret in (token, token.split(".")[1], SUB, EMAIL, NONCE):
        assert secret not in message


async def test_no_log_line_ever_contains_the_token_or_its_claims() -> None:
    """Failures are worth logging server-side, but never with the material in them."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        good = mint_id_token()
        await verify(good)
        for bad in (mint_id_token(key=WRONG_KEY), mint_unsigned_token(), mint_id_token(nonce="x")):
            with pytest.raises(InvalidIdToken):
                await verify(bad)
    finally:
        root.removeHandler(handler)

    logs = stream.getvalue()
    for secret in (good, good.split(".")[1], good.split(".")[2], NONCE, SUB, EMAIL):
        assert secret not in logs


async def test_our_own_algorithm_pinning_holds_even_without_pyjwts_safety_net(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PyJWT refuses to use an asymmetric key as an HMAC secret, which already blocks this attack.

    That guard belongs to the library, not to us. Here it is disabled, so the forged HS256 token
    would verify successfully against the public key -- unless our own explicit RS256 pinning stops
    it first. This is what makes `algorithms=["RS256"]` and the header pre-check observable.
    """
    from jwt.algorithms import HMACAlgorithm

    def unguarded_prepare_key(self: object, key: Any) -> bytes:
        if isinstance(key, bytes):
            return key
        pem: bytes = key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return pem

    monkeypatch.setattr(HMACAlgorithm, "prepare_key", unguarded_prepare_key)

    now = time.time()
    forged = mint_hmac_forged_token(
        {
            "iss": "https://accounts.google.com",
            "aud": TEST_GOOGLE_CLIENT_ID,
            "sub": SUB,
            "email": EMAIL,
            "email_verified": True,
            "nonce": NONCE,
            "iat": now,
            "exp": now + 3600,
        }
    )
    with pytest.raises(InvalidIdToken):
        await verify(forged)
