"""Turning a verified Google identity into a row in our ``users`` table.

There is no separate "register" step. The first login inserts; every later one updates. The key is
Google's ``sub``, never the email: a Google account's address can change, and an address can be
reassigned, so using it as the identity would both lose people their data and risk handing one
person another's account.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.id_token import GoogleIdentity

# `DO UPDATE`, not `DO NOTHING`: with `DO NOTHING` the `RETURNING` clause yields no row when another
# transaction inserted first, which is exactly what happens when a user opens two login tabs at
# once. `DO UPDATE` always returns the row, so concurrent first logins converge on one user.
_UPSERT_USER = text(
    "INSERT INTO users (google_sub, email) VALUES (:google_sub, :email) "
    "ON CONFLICT (google_sub) DO UPDATE "
    "SET email = EXCLUDED.email, last_login_at = now() "
    "RETURNING id"
)


async def upsert_user(db: AsyncSession, identity: GoogleIdentity) -> UUID:
    """Insert or refresh the user behind this Google identity and return their id.

    Does not commit: the caller owns the transaction, so a login that fails later leaves no trace.
    """
    result = await db.execute(
        _UPSERT_USER, {"google_sub": identity.google_sub, "email": identity.email}
    )
    user_id: UUID = result.scalar_one()
    return user_id
