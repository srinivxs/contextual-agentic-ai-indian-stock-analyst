"""The investor profile over HTTP (P13). Signed-in only; every route is scoped to the caller.

    GET    /api/v1/profile               your profile: what is remembered, and the choices the
                                          panel may offer (lets it stay in sync with the
                                          vocabulary without hard-coding it)
    PUT    /api/v1/profile/{field}        set one field from the panel (source becomes "edited")
    DELETE /api/v1/profile/{field}        forget one field (idempotent)
    DELETE /api/v1/profile                forget everything (idempotent)

On the state-changing routes the checks run in the house order: Origin, session, then validation
(an unknown field, or values that are not this field's own CHOICES) -- 422 in the shared error
envelope, never echoing the caller's raw input. One short transaction per request.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel
from pydantic import Field as PydanticField
from starlette.responses import Response

from app.auth.deps import current_user, require_same_origin
from app.auth.sessions import CurrentUser
from app.core.errors import AppError
from app.memory.store import forget_all, forget_field, get_profile, set_field
from app.memory.vocabulary import (
    CHOICES,
    FIELD_LABELS,
    FIELDS,
    SINGLE_VALUED,
    StoredPreference,
    labels,
)
from app.memory.vocabulary import (
    Field as PreferenceField,
)

router = APIRouter(prefix="/api/v1", tags=["profile"])


class ValuesIn(BaseModel):
    values: list[str] = PydanticField(min_length=1, max_length=5)


class FieldOut(BaseModel):
    field: str
    values: list[str]
    labels: list[str]
    quote: str
    source: str
    updated_at: str


class OptionOut(BaseModel):
    value: str
    label: str


class ChoiceOut(BaseModel):
    field: str
    label: str
    single: bool
    options: list[OptionOut]


class ProfileOut(BaseModel):
    fields: list[FieldOut]
    choices: list[ChoiceOut]


def _field_out(row: StoredPreference) -> FieldOut:
    return FieldOut(
        field=row.field,
        values=list(row.values),
        labels=list(labels(row.field, row.values)),
        quote=row.quote,
        source=row.source,
        updated_at=row.updated_at.isoformat(),
    )


def _unknown_field() -> AppError:
    return AppError(status_code=422, code="validation_error", message="Unknown field")


def _invalid_values() -> AppError:
    return AppError(
        status_code=422, code="validation_error", message="Invalid values for this field"
    )


def _as_field(field: str) -> PreferenceField:
    if field not in FIELDS:
        raise _unknown_field()
    return field


@router.get("/profile", summary="Your investor profile, and the choices the panel may offer")
async def profile(
    request: Request, user: Annotated[CurrentUser, Depends(current_user)]
) -> ProfileOut:
    async with request.app.state.session_factory() as db:
        rows = await get_profile(db, user.id)
    return ProfileOut(
        fields=[_field_out(row) for row in rows],
        choices=[
            ChoiceOut(
                field=field,
                label=FIELD_LABELS[field],
                single=field in SINGLE_VALUED,
                options=[
                    OptionOut(value=value, label=label) for value, label in CHOICES[field].items()
                ],
            )
            for field in FIELDS
        ],
    )


@router.put(
    "/profile/{field}",
    summary="Set one field of your profile",
    dependencies=[Depends(require_same_origin)],
)
async def set_profile_field(
    field: Annotated[str, Path()],
    body: ValuesIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> FieldOut:
    known = _as_field(field)
    async with request.app.state.session_factory() as db:
        try:
            found = await set_field(db, user.id, known, tuple(body.values))
        except ValueError as error:
            raise _invalid_values() from error
        await db.commit()
    return _field_out(found)


@router.delete(
    "/profile/{field}",
    status_code=204,
    summary="Forget one field of your profile (idempotent)",
    dependencies=[Depends(require_same_origin)],
)
async def delete_profile_field(
    field: Annotated[str, Path()],
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> Response:
    known = _as_field(field)
    async with request.app.state.session_factory() as db:
        await forget_field(db, user.id, known)
        await db.commit()
    return Response(status_code=204)


@router.delete(
    "/profile",
    status_code=204,
    summary="Forget your whole profile (idempotent)",
    dependencies=[Depends(require_same_origin)],
)
async def delete_profile(
    request: Request, user: Annotated[CurrentUser, Depends(current_user)]
) -> Response:
    async with request.app.state.session_factory() as db:
        await forget_all(db, user.id)
        await db.commit()
    return Response(status_code=204)
