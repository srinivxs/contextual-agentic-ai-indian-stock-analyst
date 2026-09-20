"""Fakes that stand in for Google: our own RSA keys, minted ID tokens, and a fake JWKS endpoint.

Tests never reach the network and never use a real Google token. Generating our own keypair means
we can mint a token that is *genuinely* signed (so a passing test proves real signature
verification) and also mint one signed by the WRONG key (so a failing test proves the check bites).
"""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from tests.helpers import TEST_GOOGLE_CLIENT_ID

GOOGLE_ISSUER = "https://accounts.google.com"
KID = "test-signing-key"
WRONG_KID = "some-other-key"

SUB = "117234567890123456789"  # shaped like a real Google `sub`, but ours
EMAIL = "ada@example.test"


def _new_key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


# Generated once: RSA key generation is slow enough to matter across a few hundred tests.
SIGNING_KEY = _new_key()  # the key our fake Google signs with
WRONG_KEY = _new_key()  # a valid RSA key that is simply not Google's


def _pem(key: RSAPrivateKey) -> Any:
    return key


def public_jwk(key: RSAPrivateKey, kid: str) -> dict[str, Any]:
    """The public half of a key, in the JSON Web Key shape Google publishes."""
    jwk: dict[str, Any] = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return jwk


def jwks_document(*keys: tuple[RSAPrivateKey, str]) -> dict[str, Any]:
    """A JWKS document listing the given (key, kid) pairs. Google normally publishes two."""
    return {"keys": [public_jwk(key, kid) for key, kid in keys]}


CURRENT_JWKS = jwks_document((SIGNING_KEY, KID))
ROTATED_JWKS = jwks_document((WRONG_KEY, WRONG_KID), (SIGNING_KEY, KID))


def mint_id_token(
    *,
    key: RSAPrivateKey | None = None,
    kid: str = KID,
    algorithm: str = "RS256",
    nonce: str = "the-expected-nonce",
    issuer: str = GOOGLE_ISSUER,
    audience: str | None = None,
    subject: str | None = SUB,
    email: str | None = EMAIL,
    email_verified: bool | None = True,
    issued_at: float | None = None,
    expires_at: float | None = None,
    now: float | None = None,
    extra: dict[str, Any] | None = None,
    drop: tuple[str, ...] = (),
) -> str:
    """An ID token shaped exactly like Google's, with any claim adjustable or removed."""
    moment = now if now is not None else time.time()
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": audience if audience is not None else TEST_GOOGLE_CLIENT_ID,
        "sub": subject,
        "email": email,
        "email_verified": email_verified,
        "nonce": nonce,
        "iat": issued_at if issued_at is not None else moment,
        "exp": expires_at if expires_at is not None else moment + 3600,
    }
    claims.update(extra or {})
    for claim in drop:
        claims.pop(claim, None)
    claims = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(claims, _pem(key or SIGNING_KEY), algorithm=algorithm, headers={"kid": kid})


def mint_unsigned_token(**kwargs: Any) -> str:
    """A token with `alg: none` and an empty signature: the classic forgery attempt."""
    token = mint_id_token(**kwargs)
    _, payload, _ = token.split(".")
    none_header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "none", "kid": KID}).encode())
        .decode()
        .rstrip("=")
    )
    return f"{none_header}.{payload}."


class FakeJwksEndpoint:
    """Stands in for `https://www.googleapis.com/oauth2/v3/certs`, under the test's control."""

    def __init__(
        self,
        document: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        status: int = 200,
    ) -> None:
        self.document = CURRENT_JWKS if document is None else document
        self.headers = headers if headers is not None else {"Cache-Control": "max-age=3600"}
        self.status = status
        self.body: str | None = None  # set to serve something that is not valid JWKS JSON
        self.failure: Exception | None = None  # set to simulate Google being unreachable
        self.calls = 0
        self.on_request: Callable[[], Awaitable[None]] | None = None  # e.g. to add a delay

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.on_request is not None:
            await self.on_request()
        if self.failure is not None:
            raise self.failure
        content = self.body if self.body is not None else json.dumps(self.document)
        return httpx.Response(self.status, content=content, headers=self.headers)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport)

    def go_down(self, error: Exception | None = None) -> None:
        self.failure = error or httpx.ConnectError("google is unreachable")

    def come_back(self, document: dict[str, Any] | None = None) -> None:
        self.failure = None
        if document is not None:
            self.document = document


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def mint_hmac_forged_token(claims: dict[str, Any], *, kid: str = KID) -> str:
    """The RS256->HS256 confusion attack, built by hand.

    An attacker takes Google's PUBLIC key (which anyone can fetch) and uses its bytes as an HMAC
    shared secret. A verifier that let the token's header choose the algorithm would then "verify"
    it successfully. PyJWT's own `encode` refuses to do this, so the token is assembled directly --
    which is what a real attacker would do anyway.
    """
    public_pem = SIGNING_KEY.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": kid}).encode())
    payload = _b64(json.dumps(claims).encode())
    signature = hmac.new(public_pem, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(signature)}"
