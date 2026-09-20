"""Server-side sessions.

The browser holds a random token; the database holds only its SHA-256 hash. So a copy of the
database (a backup, a read-only injection) cannot be turned into working cookies. A plain fast hash
is enough because the token has 256 random bits: there is nothing to guess or brute-force, unlike a
human-chosen password.

None of these functions commit. The caller owns the transaction, so a login that fails half way
leaves nothing behind.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SESSION_TOKEN_BYTES = 32  # 256 bits

_INSERT_SESSION = text(
    "INSERT INTO sessions (user_id, token_hash, expires_at) "
    "VALUES (:user_id, :token_hash, now() + CAST(:lifetime AS interval))"
)

# The expiry check lives in SQL, next to the lookup, so an expired row can never be returned.
_FIND_USER = text(
    "SELECT u.id, u.email FROM sessions s JOIN users u ON u.id = s.user_id "
    "WHERE s.token_hash = :token_hash AND s.expires_at > now()"
)

_DELETE_SESSION = text("DELETE FROM sessions WHERE token_hash = :token_hash")


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    email: str


def generate_session_token() -> str:
    """A new unguessable token: 32 bytes from the operating system's secure random source."""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(token: str) -> bytes:
    """The only form of a token that is ever stored: its SHA-256 digest (32 bytes)."""
    return hashlib.sha256(token.encode("utf-8")).digest()


async def create_session(db: AsyncSession, *, user_id: UUID, lifetime: timedelta) -> str:
    """Store a new session for the user and return the raw token, to be handed out exactly once.

    ``expires_at`` is set from the database clock and never changed afterwards.
    """
    token = generate_session_token()
    await db.execute(
        _INSERT_SESSION,
        {"user_id": user_id, "token_hash": hash_session_token(token), "lifetime": lifetime},
    )
    return token


async def get_current_user(db: AsyncSession, token: str) -> CurrentUser | None:
    """The user this token belongs to, or None if it is unknown, altered or expired."""
    row = (await db.execute(_FIND_USER, {"token_hash": hash_session_token(token)})).one_or_none()
    return CurrentUser(id=row.id, email=row.email) if row is not None else None


async def delete_session(db: AsyncSession, token: str) -> None:
    """Remove the session. Deleting one that does not exist is not an error."""
    await db.execute(_DELETE_SESSION, {"token_hash": hash_session_token(token)})
