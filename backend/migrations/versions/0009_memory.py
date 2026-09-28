"""investor_profiles: a small structured memory of the user's own stated preferences (P13)

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-28

investor_profiles: at most one row per (user, field). ``field`` is one of the fixed vocabulary's
four fields (app/memory/vocabulary.py); ``tags`` holds that field's chosen values ("values" is a
reserved word, so the column is named ``tags``); ``quote`` is the user's own sentence (or, for a
field set from the panel, the chosen labels joined together); ``source`` says whether the row came
from a chat message or an edit in the panel. A newer ``remember`` for a field replaces the whole
row (the merge rule): other fields are untouched.

The four field-specific CHECKs below are the vocabulary's CHOICES copied as of this migration, on
purpose: a migration must not import application code that can change later (a later change to the
vocabulary needs its own migration). ``tests/unit/test_memory_vocabulary.py`` (P13) checks this
copy still equals ``app.memory.vocabulary.CHOICES``.

Also widens ``messages.status`` (0008) to accept 'remembered': a chat message that only stated
preferences, saved with no LLM call (app/chat/contract.py's ReplyStatus).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FIELDS = "'risk_preference', 'debt_preference', 'investment_style', 'other_preferences'"

OLD_MESSAGE_STATUS = (
    "(role = 'user' AND status IS NULL) "
    "OR (role = 'assistant' AND status IS NOT NULL "
    "AND status IN ('answered', 'abstained', 'out_of_scope'))"
)
NEW_MESSAGE_STATUS = (
    "(role = 'user' AND status IS NULL) "
    "OR (role = 'assistant' AND status IS NOT NULL "
    "AND status IN ('answered', 'abstained', 'out_of_scope', 'remembered'))"
)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE investor_profiles (
            user_id uuid NOT NULL,
            field text NOT NULL,
            tags text[] NOT NULL,
            quote text NOT NULL,
            source text NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_investor_profiles PRIMARY KEY (user_id, field),
            CONSTRAINT fk_investor_profiles_user_id_users FOREIGN KEY (user_id)
                REFERENCES users (id) ON DELETE CASCADE,
            CONSTRAINT ck_investor_profiles_field CHECK (field IN ({FIELDS})),
            CONSTRAINT ck_investor_profiles_source CHECK (source IN ('chat', 'edited')),
            CONSTRAINT ck_investor_profiles_quote_length CHECK (
                char_length(quote) BETWEEN 1 AND 300
            ),
            -- cardinality(), not array_length(): array_length() of an empty array is NULL, and a
            -- CHECK passes an unknown comparison, so an empty array would slip through.
            CONSTRAINT ck_investor_profiles_tags_cardinality CHECK (
                cardinality(tags) BETWEEN 1 AND 5
            ),
            CONSTRAINT ck_investor_profiles_tags_no_nulls CHECK (
                array_position(tags, NULL) IS NULL
            ),
            -- risk_preference and debt_preference are single-valued (SINGLE_VALUED in the
            -- vocabulary); investment_style and other_preferences may hold more than one.
            CONSTRAINT ck_investor_profiles_tags_single_valued CHECK (
                field NOT IN ('risk_preference', 'debt_preference') OR cardinality(tags) = 1
            ),
            -- Each field's tags must come from that field's own CHOICES (copied above the
            -- upgrade/downgrade functions; kept equal to app.memory.vocabulary.CHOICES by a test).
            CONSTRAINT ck_investor_profiles_tags_allowed CHECK (
                (field = 'risk_preference'
                 AND tags <@ ARRAY['conservative', 'moderate', 'aggressive'])
                OR (field = 'debt_preference'
                    AND tags <@ ARRAY['avoid_high_debt', 'debt_ok'])
                OR (field = 'investment_style'
                    AND tags <@ ARRAY['income', 'growth', 'quality', 'value', 'momentum'])
                OR (field = 'other_preferences'
                    AND tags <@ ARRAY['long_term', 'short_term', 'stability'])
            )
        )
        """
    )

    op.drop_constraint("ck_messages_status", "messages", type_="check")
    op.create_check_constraint("ck_messages_status", "messages", NEW_MESSAGE_STATUS)


def downgrade() -> None:
    # 'remembered' rows only ever exist locally or in tests (CHAT_ENABLED is off in production
    # until Go-live); deleting them here is safe and lets the old, narrower CHECK be restored.
    op.execute("DELETE FROM messages WHERE role = 'assistant' AND status = 'remembered'")
    op.drop_constraint("ck_messages_status", "messages", type_="check")
    op.create_check_constraint("ck_messages_status", "messages", OLD_MESSAGE_STATUS)

    op.drop_table("investor_profiles")
