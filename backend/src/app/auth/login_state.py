"""The three values that belong to ONE login attempt, and the cookie that carries them.

Between the redirect to Google and the callback we must remember: the `state` we expect back, the
`nonce` Google will copy into the ID token, and the PKCE `code_verifier`. Rather than a database
row we put them in a short-lived **signed** cookie, so there is nothing to clean up.

Signed, not encrypted: anyone holding the cookie can decode the payload. That is acceptable only
because it holds nothing but this attempt's single-use values -- never a secret of ours. What the
signature buys is the thing that matters: we can tell whether *we* issued this cookie, unchanged,
recently. Without that check an attacker could hand a victim a callback URL carrying the attacker's
own `state` and code, and silently sign the victim into the attacker's account.
"""

import secrets
from dataclasses import asdict, dataclass

from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.auth.pkce import generate_code_verifier
from app.core.config import Settings

# Namespaces the signature: a value signed elsewhere with the same secret cannot be replayed here.
_SALT = "oauth-login-state"


class InvalidLoginState(Exception):
    """The login cookie is missing, altered, signed with another key, or too old.

    Deliberately one exception with one message for every cause: the caller turns it into a single
    generic error, so the browser cannot use it to learn which check failed.
    """


@dataclass(frozen=True)
class LoginAttempt:
    state: str
    nonce: str
    code_verifier: str


def new_login_attempt() -> LoginAttempt:
    """Three independent random values. Reusing one for two purposes would defeat both checks."""
    return LoginAttempt(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        code_verifier=generate_code_verifier(),
    )


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret.get_secret_value(), salt=_SALT)


def sign_login_attempt(attempt: LoginAttempt, settings: Settings) -> str:
    """The cookie value: the attempt, plus a timestamp, plus a signature over both."""
    return _serializer(settings).dumps(asdict(attempt))


def read_login_attempt(token: str, settings: Settings) -> LoginAttempt:
    """Verify and return the attempt, or raise ``InvalidLoginState``.

    ``max_age`` makes a stale cookie fail exactly like a forged one, so a login tab left open for
    hours cannot still be completed.
    """
    try:
        payload = _serializer(settings).loads(token, max_age=settings.oauth_login_ttl_seconds)
        return LoginAttempt(
            state=payload["state"],
            nonce=payload["nonce"],
            code_verifier=payload["code_verifier"],
        )
    except (BadSignature, KeyError, TypeError, ValueError):
        # `from None`: itsdangerous puts the offending value in its message, and that message must
        # never reach a log line or a traceback we render.
        raise InvalidLoginState("invalid login state") from None
