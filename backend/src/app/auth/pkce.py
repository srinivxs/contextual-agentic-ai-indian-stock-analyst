"""PKCE: Proof Key for Code Exchange (RFC 7636).

The authorization code comes back through the user's browser, so it can end up in history, a
`Referer` header or a proxy log. PKCE makes a leaked code useless on its own:

1. we invent a random **verifier** and keep it on the server;
2. we send Google only its SHA-256 hash, the **challenge**;
3. when we exchange the code we present the verifier, and Google checks it hashes to the challenge.

Only the party that started the login can finish it. This is separate from, and additional to, the
client secret: the secret authenticates *our server*, PKCE binds the code to *this login attempt*.
"""

import base64
import hashlib
import secrets

# RFC 7636 section 4.1 allows 43..128 characters; `token_urlsafe(32)` yields 43 (256 bits) from the
# unreserved alphabet, which is the minimum length and already far beyond guessing.
CODE_VERIFIER_BYTES = 32

CHALLENGE_METHOD = "S256"  # never "plain": that would send the verifier itself through the browser


def _base64url_nopad(raw: bytes) -> str:
    # RFC 7636 appendix A: base64url with the '=' padding stripped.
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def generate_code_verifier() -> str:
    """A fresh, unguessable verifier for one login attempt. It never leaves the server."""
    return secrets.token_urlsafe(CODE_VERIFIER_BYTES)


def code_challenge(verifier: str) -> str:
    """The value we show Google: ``base64url(sha256(verifier))``, unpadded.

    A hash, so Google (or anyone reading the authorization URL) cannot work back to the verifier.
    """
    return _base64url_nopad(hashlib.sha256(verifier.encode("ascii")).digest())
