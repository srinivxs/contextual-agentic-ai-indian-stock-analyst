"""Talking to Google: for now, only the URL we send the browser to.

P4b-1 builds the authorization request. The code exchange, JWKS fetching and ID-token verification
arrive in P4b-2 and P4b-3.

Endpoints are Google's published OIDC values. They are constants rather than a runtime fetch of the
discovery document: one fewer network dependency on every login, at the cost of a doc change if
Google ever moves them (they have not).
"""

from urllib.parse import urlencode

from app.auth.login_state import LoginAttempt
from app.auth.pkce import CHALLENGE_METHOD, code_challenge
from app.core.config import Settings

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105  (a URL, not a secret)
GOOGLE_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"  # used from P4b-2
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")  # both are valid

# The least Google will give us: an identity (`openid` -> `sub`) and an address. No `profile`, so
# no name or picture we would then have to store and protect.
GOOGLE_SCOPES = ("openid", "email")

CALLBACK_PATH = "/api/v1/auth/google/callback"


def redirect_uri(settings: Settings) -> str:
    """Where Google sends the browser back.

    Built from the configured public origin, never from the request's `Host` or `X-Forwarded-*`
    headers, which a caller can set. Google matches this string against the registered one exactly,
    so it must be byte-for-byte stable.
    """
    return f"{settings.public_origin}{CALLBACK_PATH}"


def authorization_url(settings: Settings, attempt: LoginAttempt) -> str:
    """The URL we redirect the browser to. It travels in the address bar: nothing secret here.

    The client secret is not part of this request; it is presented server-to-server when the code
    is exchanged. Only the PKCE *challenge* is sent, never the verifier.
    """
    query = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri(settings),
        "response_type": "code",  # authorization-code flow; never an implicit one
        "scope": " ".join(GOOGLE_SCOPES),
        "state": attempt.state,  # comes back in the callback and must match
        "nonce": attempt.nonce,  # comes back inside the ID token and must match
        "code_challenge": code_challenge(attempt.code_verifier),
        "code_challenge_method": CHALLENGE_METHOD,
        # Let the user pick an account instead of being silently reused; helps anyone with more
        # than one Google account, and makes switching possible after logout.
        "prompt": "select_account",
    }
    # No `access_type=offline`, so Google issues no refresh token: we never call a Google API.
    return f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{urlencode(query)}"
