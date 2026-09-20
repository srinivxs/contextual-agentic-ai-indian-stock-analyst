"""users and sessions

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-20

The two tables behind Google sign-in and server-side sessions (ADR 012, written in P4c).

* ``users`` keeps only what we need from Google: ``google_sub`` (the stable account identifier, and
  the identity) and ``email`` (display and contact; NOT unique and never used to find a user,
  because a Google account's email can change). No name, picture, locale or tokens.
* ``sessions`` stores a SHA-256 hash of the session token, never the token itself, and a fixed
  ``expires_at``. There is no ``last_seen_at``: sessions are not renewed by use. Logging out
  deletes the row.

Production migrations are forward-only. ``downgrade`` exists so local development and tests can
prove this migration is reversible (up, down, up).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("google_sub", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_login_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("google_sub", name="uq_users_google_sub"),
        sa.CheckConstraint("length(btrim(google_sub)) > 0", name="ck_users_google_sub_not_blank"),
        sa.CheckConstraint("length(btrim(email)) > 0", name="ck_users_email_not_blank"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        # Deleting a user removes their sessions with them.
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_sessions_user_id_users", ondelete="CASCADE"
        ),
        # Looked up by hash on every authenticated request: the unique constraint is its index.
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        # A SHA-256 digest is exactly 32 bytes: anything else is not a hash of a session token.
        sa.CheckConstraint("octet_length(token_hash) = 32", name="ck_sessions_token_hash_length"),
        sa.CheckConstraint("expires_at > created_at", name="ck_sessions_expires_after_created"),
    )
    # The foreign key's lookups and cascades, and the future purge of expired sessions.
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("sessions")  # its indexes and constraints go with it
    op.drop_table("users")
