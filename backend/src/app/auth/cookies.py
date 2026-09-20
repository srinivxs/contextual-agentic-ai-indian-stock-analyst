"""The session cookie: one place decides every attribute, for setting and for clearing.

Always HttpOnly (JavaScript can never read it), SameSite=Lax (not sent on cross-site requests
except top-level GET navigations, which blocks cross-site POST/PUT/DELETE), Path=/, and host-only
(no Domain attribute). `Secure` and the cookie name come from settings: in production the name is
``__Host-session`` and Secure is mandatory.
"""

from starlette.responses import Response

from app.core.config import Settings

# The short-lived cookie that remembers ONE login attempt while the user is away at Google.
LOGIN_COOKIE_NAME = "oauth_login"
# Scoped to the Google auth endpoints, so it is not sent with every other request. That scoping is
# also why it cannot use the `__Host-` prefix, which requires Path=/.
LOGIN_COOKIE_PATH = "/api/v1/auth/google"


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=int(settings.session_lifetime.total_seconds()),
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    # Same attributes as when it was set: a `__Host-` cookie can only be replaced or deleted by a
    # header that satisfies the same prefix rules.
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def set_login_cookie(response: Response, signed_attempt: str, settings: Settings) -> None:
    """Remember the login attempt for as long as the user has to finish signing in at Google."""
    response.set_cookie(
        key=LOGIN_COOKIE_NAME,
        value=signed_attempt,
        max_age=settings.oauth_login_ttl_seconds,
        path=LOGIN_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        # Lax, never Strict: the callback is a top-level navigation FROM google.com, and a Strict
        # cookie would not be sent on it, so every login would fail.
        samesite="lax",
    )


def clear_login_cookie(response: Response, settings: Settings) -> None:
    """One attempt, one cookie: cleared whether the login succeeded or failed."""
    response.delete_cookie(
        key=LOGIN_COOKIE_NAME,
        path=LOGIN_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
