"""FastAPI dependencies for authentication.

``current_user`` identifies the caller from the session cookie. ``require_same_origin`` is the CSRF
guard for state-changing routes. Both are small on purpose: SameSite=Lax cookies plus this Origin
check replace CSRF-token machinery.
"""

from fastapi import Request

from app.auth.sessions import CurrentUser, get_current_user
from app.core.errors import AppError

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _not_authenticated() -> AppError:
    # One message for every failure: unknown, altered, expired, or missing. It reveals nothing.
    return AppError(status_code=401, code="unauthorized", message="Not authenticated")


async def current_user(request: Request) -> CurrentUser:
    settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        # No cookie, nothing to look up: not even a database session is opened.
        raise _not_authenticated()
    # A short-lived session, closed straight after the lookup so its connection returns to the pool.
    async with request.app.state.session_factory() as db:
        user = await get_current_user(db, token)
    if user is None:
        raise _not_authenticated()
    return user


async def require_same_origin(request: Request) -> None:
    """Reject a state-changing request that a browser says came from another origin.

    Browsers always send `Origin` on cross-site POST/PUT/PATCH/DELETE, so a mismatch (including the
    literal `null` that sandboxed frames send) is refused. A request with no `Origin` header comes
    from a non-browser client such as curl or a test, which cannot be a cross-site forgery.
    Safe methods are never checked.
    """
    if request.method not in _UNSAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is not None and origin != request.app.state.settings.public_origin:
        raise AppError(status_code=403, code="forbidden", message="Cross-origin request rejected")
