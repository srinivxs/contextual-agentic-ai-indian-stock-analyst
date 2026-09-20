"""Authentication endpoints available in P4a: who am I, and log out.

Logging in (Google) arrives in P4b. Everything here works on a session that already exists.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from starlette.responses import Response

from app.auth.cookies import clear_session_cookie
from app.auth.deps import current_user, require_same_origin
from app.auth.sessions import CurrentUser, delete_session

router = APIRouter(prefix="/api/v1", tags=["auth"])


class MeResponse(BaseModel):
    id: UUID
    email: str


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
