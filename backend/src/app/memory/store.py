"""The investor profile in the database: at most one row per (user, field) (P13, migration 0009).

Plain SQL, like the rest of the data access (ADR 013). None of these functions commits; the caller
owns the transaction. Every statement is scoped to one user's id, so a request can never read or
change someone else's profile.

The merge rule lives in ``remember``'s ``ON CONFLICT ... DO UPDATE``: a newer statement about a
field replaces that field's row entirely (the database's own primary key, ``(user_id, field)``,
makes this atomic even under concurrent ``remember`` calls for the same user).
"""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.vocabulary import FIELDS, Field, Preference, StoredPreference, labels, valid_values

_GET = text(
    "SELECT field, tags, quote, source, updated_at FROM investor_profiles WHERE user_id = :user_id"
)

_REMEMBER = text(
    "INSERT INTO investor_profiles (user_id, field, tags, quote, source) "
    "VALUES (:user_id, :field, :tags, :quote, 'chat') "
    "ON CONFLICT (user_id, field) DO UPDATE SET "
    "tags = EXCLUDED.tags, quote = EXCLUDED.quote, source = 'chat', updated_at = now()"
)

_SET_FIELD = text(
    "INSERT INTO investor_profiles (user_id, field, tags, quote, source) "
    "VALUES (:user_id, :field, :tags, :quote, 'edited') "
    "ON CONFLICT (user_id, field) DO UPDATE SET "
    "tags = EXCLUDED.tags, quote = EXCLUDED.quote, source = 'edited', updated_at = now() "
    "RETURNING tags, quote, source, updated_at"
)

_FORGET_FIELD = text("DELETE FROM investor_profiles WHERE user_id = :user_id AND field = :field")

_FORGET_ALL = text("DELETE FROM investor_profiles WHERE user_id = :user_id")


def _stored(row: Any) -> StoredPreference:
    return StoredPreference(
        field=row.field,
        values=tuple(row.tags),
        quote=row.quote,
        source=row.source,
        updated_at=row.updated_at,
    )


async def get_profile(db: AsyncSession, user_id: UUID) -> list[StoredPreference]:
    """Every field the user has, in the vocabulary's fixed order (not insertion order)."""
    result = await db.execute(_GET, {"user_id": user_id})
    by_field = {row.field: _stored(row) for row in result}
    return [by_field[field] for field in FIELDS if field in by_field]


async def remember(db: AsyncSession, user_id: UUID, preferences: list[Preference]) -> None:
    """Store what one chat message said. Each preference replaces that field's row; fields the
    message did not mention are left exactly as they were."""
    for preference in preferences:
        await db.execute(
            _REMEMBER,
            {
                "user_id": user_id,
                "field": preference.field,
                "tags": list(preference.values),
                "quote": preference.quote[:300],
            },
        )


async def set_field(
    db: AsyncSession, user_id: UUID, field: Field, values: tuple[str, ...]
) -> StoredPreference:
    """Set one field from the panel. The quote becomes the chosen labels, since there is no
    sentence to keep. Raises ``ValueError`` if the values are not valid for this field."""
    if not valid_values(field, values):
        raise ValueError(f"Invalid values for {field}: {values!r}")
    row = (
        await db.execute(
            _SET_FIELD,
            {
                "user_id": user_id,
                "field": field,
                "tags": list(values),
                "quote": ", ".join(labels(field, values)),
            },
        )
    ).one()
    return StoredPreference(
        field=field, values=tuple(row.tags), quote=row.quote, source=row.source,
        updated_at=row.updated_at,
    )  # fmt: skip


async def forget_field(db: AsyncSession, user_id: UUID, field: Field) -> None:
    """Idempotent: forgetting an already-absent field does nothing."""
    await db.execute(_FORGET_FIELD, {"user_id": user_id, "field": field})


async def forget_all(db: AsyncSession, user_id: UUID) -> None:
    await db.execute(_FORGET_ALL, {"user_id": user_id})
