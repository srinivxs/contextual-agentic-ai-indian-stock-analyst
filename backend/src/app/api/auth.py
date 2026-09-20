"""Authentication endpoints: sign in with Google, who am I, sign out.

The callback is the security-critical one. Its order is deliberate and is enforced by the shape of
the code below: **every check that can be made without talking to Google is made first**, so a
forged or stale callback costs us nothing and tells an attacker nothing. The database is opened
only once authentication has fully succeeded.
"""

import logging
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from starlette.responses import RedirectResponse, Response

from app.auth.cookies import (
    LOGIN_COOKIE_NAME,
    clear_login_cookie,
    clear_session_cookie,
    set_login_cookie,
    set_session_cookie,
)
from app.auth.deps import current_user, require_same_origin
from app.auth.google import TokenExchangeFailed, authorization_url, exchange_code
from app.auth.id_token import GoogleIdentity, InvalidIdToken, verify_id_token
from app.auth.login_state import (
    InvalidLoginState,
    LoginAttempt,
    new_login_attempt,
    read_login_attempt,
    sign_login_attempt,
)
from app.auth.sessions import CurrentUser, create_session, delete_session, purge_expired_sessions
from app.auth.users import upsert_user
from app.core.config import Settings

logger = logging.getLogger("app.auth")

router = APIRouter(prefix="/api/v1", tags=["auth"])

# What the browser is told. Never which check failed, and never anything Google said.
GENERIC_FAILURE = "login_failed"
CANCELLED = "cancelled"


class MeResponse(BaseModel):
    id: UUID
    email: str


class _LoginFailed(Exception):
    """Internal: ends the login. ``reason`` is logged; ``shown`` is all the browser learns."""

    def __init__(self, reason: str, shown: str = GENERIC_FAILURE) -> None:
        super().__init__(reason)
        self.reason = reason
        self.shown = shown


@router.get("/me", summary="The signed-in user")
async def me(user: Annotated[CurrentUser, Depends(current_user)]) -> MeResponse:
    return MeResponse(id=user.id, email=user.email)


@router.post(
    "/auth/logout",
    status_code=204,
    summary="End the current session",
    dependencies=[Depends(require_same_origin)],
)
async def logout(request: Request) -> Response:
    settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name)
    if token:  # no cookie means no session to end, and no database access
        async with request.app.state.session_factory() as db:
            await delete_session(db, token)
            await db.commit()
    # Always clear the cookie, so "you are logged out" is true whether or not you were logged in.
    response = Response(status_code=204)
    clear_session_cookie(response, settings)
    return response


@router.get("/auth/google/login", summary="Start signing in with Google")
async def google_login(request: Request) -> Response:
    """Mint one login attempt, remember it in a signed cookie, and send the browser to Google.

    No database, no network: this is a redirect and a `Set-Cookie`.
    """
    settings: Settings = request.app.state.settings
    attempt = new_login_attempt()
    response = RedirectResponse(authorization_url(settings, attempt), status_code=302)
    set_login_cookie(response, sign_login_attempt(attempt, settings), settings)
    return response


@router.get("/auth/google/callback", summary="Finish signing in with Google")
async def google_callback(request: Request) -> Response:
    """Google has sent the browser back. Prove it really is our login, then sign the user in."""
    settings: Settings = request.app.state.settings

    try:
        raw_id_token, attempt = await _verified_exchange(request, settings)
        # The signature check. Nothing below this line runs on an unverified token.
        identity = await verify_id_token(
            raw_id_token,
            jwks=request.app.state.jwks,
            settings=settings,
            nonce=attempt.nonce,
        )
    except _LoginFailed as failure:
        return _failed(settings, failure.reason, failure.shown)
    except InvalidIdToken:
        return _failed(settings, "invalid_id_token")

    # Authentication has succeeded. Only now is a transaction opened, and it makes no network call:
    # everything that could reach out has already finished.
    token = await _sign_in(request, settings, identity)

    response = RedirectResponse(str(settings.public_base_url), status_code=302)
    set_session_cookie(response, token, settings)
    clear_login_cookie(response, settings)  # one attempt, one cookie
    return response


async def _verified_exchange(request: Request, settings: Settings) -> tuple[str, LoginAttempt]:
    """Everything that must happen BEFORE a single request goes to Google, in order.

    Reading the query directly (rather than as declared parameters) means unexpected parameters are
    ignored outright: there is nothing a caller can add that changes where we send them afterwards.
    """
    params = request.query_params

    # 1. Google itself reported a problem, most often because the user pressed cancel.
    error = params.get("error")
    if error:
        raise _LoginFailed(
            "google_error", CANCELLED if error == "access_denied" else GENERIC_FAILURE
        )

    # 2. Is this a login *we* started, unaltered and recent? Without this check an attacker could
    #    send a victim a callback carrying the attacker's own code, signing them into the wrong
    #    account.
    signed = request.cookies.get(LOGIN_COOKIE_NAME)
    if not signed:
        raise _LoginFailed("no_login_cookie")
    try:
        attempt = read_login_attempt(signed, settings)
    except InvalidLoginState:
        raise _LoginFailed("bad_login_cookie") from None

    # 3. Does the state Google handed back match the one we issued to this browser?
    if not secrets.compare_digest(params.get("state") or "", attempt.state):
        raise _LoginFailed("state_mismatch")

    # 4. Something to exchange.
    code = params.get("code") or ""
    if not code:
        raise _LoginFailed("no_code")

    # 5. Only now does anything leave our server.
    try:
        raw_id_token = await exchange_code(
            code,
            client=request.app.state.http_client,
            settings=settings,
            code_verifier=attempt.code_verifier,  # kept on the server the whole time
        )
    except TokenExchangeFailed:
        raise _LoginFailed("exchange_failed") from None
    return raw_id_token, attempt


async def _sign_in(request: Request, settings: Settings, identity: GoogleIdentity) -> str:
    """One short transaction: record the user, tidy up, issue a session. No network calls here."""
    async with request.app.state.session_factory() as db:
        user_id = await upsert_user(db, identity)
        await purge_expired_sessions(db)
        token = await create_session(db, user_id=user_id, lifetime=settings.session_lifetime)
        await db.commit()  # all three, or none
    return token


def _failed(settings: Settings, reason: str, shown: str = GENERIC_FAILURE) -> Response:
    """Log what actually happened; tell the browser only that the login did not work."""
    logger.warning("login_failed", extra={"reason": reason})
    base = str(settings.public_base_url)
    separator = "&" if "?" in base else "?"
    # The target is built from configuration alone: there is no parameter a caller can steer.
    response = RedirectResponse(f"{base}{separator}login_error={shown}", status_code=302)
    clear_login_cookie(response, settings)
    return response
