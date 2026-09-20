"""The session cookie: one place decides every attribute, for setting and for clearing.

Always HttpOnly (JavaScript can never read it), SameSite=Lax (not sent on cross-site requests
except top-level GET navigations, which blocks cross-site POST/PUT/DELETE), Path=/, and host-only
(no Domain attribute). `Secure` and the cookie name come from settings: in production the name is
``__Host-session`` and Secure is mandatory.
"""

from starlette.responses import Response

from app.core.config import Settings


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
