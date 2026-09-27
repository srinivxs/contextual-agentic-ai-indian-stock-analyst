"""conversations and messages: the chat's history (P12)

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-27

conversations: one per chat thread, owned by one user, gone when the user goes. The title is the
first question, on one line, cut to 80 characters. updated_at moves with every exchange, so the
list can show the most recent conversations first.

messages: the user's question or the assistant's reply, in the order asked. Only a reply has a
status (answered, abstained, out_of_scope), and a reply must have one. A reply keeps its numbered
sources (what its [n] markers point at) and the tokens AWS billed for it: the sum of those tokens
is the chat's spending record, read against CHAT_BUDGET_USD before every question.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE conversations (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL,
            title text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_conversations PRIMARY KEY (id),
            CONSTRAINT fk_conversations_user_id_users FOREIGN KEY (user_id)
                REFERENCES users (id) ON DELETE CASCADE,
            CONSTRAINT ck_conversations_title_length CHECK (char_length(title) BETWEEN 1 AND 80)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_conversations_user_updated ON conversations (user_id, updated_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE messages (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            conversation_id uuid NOT NULL,
            role text NOT NULL,
            text text NOT NULL,
            status text,
            sources jsonb NOT NULL DEFAULT '[]'::jsonb,
            model text,
            input_tokens integer NOT NULL DEFAULT 0,
            output_tokens integer NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_messages PRIMARY KEY (id),
            CONSTRAINT fk_messages_conversation_id_conversations FOREIGN KEY (conversation_id)
                REFERENCES conversations (id) ON DELETE CASCADE,
            CONSTRAINT ck_messages_role CHECK (role IN ('user', 'assistant')),
            -- "status IN (...)" alone would let a reply with no status through: NULL IN (...) is
            -- unknown, and a CHECK passes unknown. So the NULL case is spelled out.
            CONSTRAINT ck_messages_status CHECK (
                (role = 'user' AND status IS NULL)
                OR (role = 'assistant' AND status IS NOT NULL
                    AND status IN ('answered', 'abstained', 'out_of_scope'))
            ),
            CONSTRAINT ck_messages_text_length CHECK (char_length(text) BETWEEN 1 AND 8000),
            CONSTRAINT ck_messages_tokens CHECK (input_tokens >= 0 AND output_tokens >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_messages_conversation_created ON messages (conversation_id, created_at)"
    )


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("conversations")
