"""Verifying Google's ID token, the one thing that tells us who just signed in.

The token arrives over TLS straight from Google's token endpoint, but we still verify it
cryptographically. Checking the signature costs almost nothing and means that a mistake elsewhere
-- a mixed-up response, a proxy, a later refactor that carries the token somewhere less trusted --
cannot become an account takeover.

**There is exactly one `return` below, and it is only reachable after ``jwt.decode`` has verified an
RS256 signature made by a key Google published.** If Google's key endpoint is unreachable and we
hold no usable key, verification fails: it is never skipped.
"""

import logging
import secrets
from dataclasses import dataclass

import jwt

from app.auth.google import GOOGLE_ISSUERS
from app.auth.jwks import JwksCache, NoUsableKey
from app.core.config import Settings

logger = logging.getLogger("app.auth.id_token")

# Our clock and Google's will never agree exactly, so `exp` and `iat` get a minute of slack.
CLOCK_SKEW_LEEWAY_SECONDS = 60

# Google signs ID tokens with RS256. Pinning it here is what defeats the two classic JWT forgeries:
# `alg: none`, and re-signing with HS256 using the (public!) verification key as a shared secret.
ALGORITHM = "RS256"

# Claims that must simply be present. `email_verified` is checked for its value further down.
REQUIRED_CLAIMS = ("exp", "iss", "aud", "sub", "nonce", "email", "email_verified")


class InvalidIdToken(Exception):
    """The token is not a valid, current, Google-signed token for this app and this login.

    One opaque message for every cause, so a caller (and ultimately a browser) cannot use it to
    learn which check failed. The specific reason is logged server-side instead.
    """


@dataclass(frozen=True)
class GoogleIdentity:
    """Everything we keep from Google: the stable account id, and the address."""

    google_sub: str
    email: str


def _reject(reason: str) -> InvalidIdToken:
    # `reason` is a fixed label chosen here, never anything from the token itself.
    logger.warning("id_token_rejected", extra={"reason": reason})
    return InvalidIdToken("invalid id token")


async def verify_id_token(
    raw_token: str, *, jwks: JwksCache, settings: Settings, nonce: str
) -> GoogleIdentity:
    """Verify signature then claims, and return the identity. Raise ``InvalidIdToken`` otherwise."""
    try:
        header = jwt.get_unverified_header(raw_token)
    except Exception:
        raise _reject("unreadable_header") from None

    # Decided by us, never by the token: reject anything but RS256 before doing any work. The real
    # enforcement is `algorithms=[ALGORITHM]` in `jwt.decode` below; this is defence in depth.
    if header.get("alg") != ALGORITHM:
        raise _reject("unexpected_algorithm")
    kid = header.get("kid")
    if not kid:
        # Without a key id we cannot know which key to use, and must not try them all.
        raise _reject("missing_kid")

    try:
        signing_key = await jwks.get_key(kid)
    except NoUsableKey:
        # Google unreachable, key rotated away, or a forged `kid`. Either way: no verification.
        raise _reject("no_signing_key") from None

    try:
        claims = jwt.decode(
            raw_token,
            signing_key.key,
            algorithms=[ALGORITHM],
            audience=settings.google_client_id,  # a token minted for another app is not ours
            leeway=CLOCK_SKEW_LEEWAY_SECONDS,
            options={
                "verify_signature": True,
                "require": list(REQUIRED_CLAIMS),
            },
        )
    except Exception:
        raise _reject("signature_or_claims") from None

    # `jwt.decode` has now checked: the signature, `exp` (with leeway), `aud`, a future `iat`
    # beyond the leeway, and the presence of every required claim. What is left is ours.

    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise _reject("unexpected_issuer")

    token_nonce = claims.get("nonce")
    if not isinstance(token_nonce, str) or not secrets.compare_digest(token_nonce, nonce):
        # Ties this token to the login attempt that started minutes ago in this browser.
        raise _reject("nonce_mismatch")

    if claims.get("email_verified") is not True:
        # An address the user has not proved they own must never become an identity.
        raise _reject("email_not_verified")

    google_sub = str(claims.get("sub", "")).strip()
    email = str(claims.get("email", "")).strip()
    if not google_sub or not email:
        raise _reject("incomplete_identity")

    return GoogleIdentity(google_sub=google_sub, email=email)
