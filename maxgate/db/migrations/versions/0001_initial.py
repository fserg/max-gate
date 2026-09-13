"""Initial Account, Session, ChatLink and MessageLink schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("tg_bot_token_enc", sa.Text(), nullable=False),
        sa.Column("owner_tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("inbox_mode", sa.String(), nullable=False),
        sa.Column("inbox_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("relay_channels", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("state_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("inbox_mode IN ('private', 'supergroup')"),
        sa.CheckConstraint(
            "state IN ('new','logging_in','password_required','active','paused','session_lost','error')"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "account_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "chat_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("max_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("max_chat_type", sa.String(), nullable=False),
        sa.Column("max_title", sa.String(), nullable=True),
        sa.Column("topic_id", sa.BigInteger(), nullable=True),
        sa.Column("renamed_by_owner", sa.Boolean(), nullable=False),
        sa.Column("muted", sa.Boolean(), nullable=False),
        sa.Column("last_relayed_time", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "max_chat_id"),
    )
    op.create_index(
        "ix_chat_links_account_topic", "chat_links", ["account_id", "topic_id"], unique=False
    )
    op.create_table(
        "max_sessions",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("token_enc", sa.Text(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("mt_instance_id", sa.String(), nullable=False),
        sa.Column("user_agent_json", sa.JSON(), nullable=True),
        sa.Column("sync_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id"),
    )
    op.create_table(
        "max_users",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id", "user_id"),
    )
    op.create_table(
        "message_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("chat_link_id", sa.Integer(), nullable=False),
        sa.Column("max_message_id", sa.BigInteger(), nullable=False),
        sa.Column("tg_message_id", sa.BigInteger(), nullable=False),
        sa.Column("part", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("direction IN ('max_to_tg','tg_to_max')"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_link_id"], ["chat_links.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_message_links_account_tg",
        "message_links",
        ["account_id", "tg_message_id"],
        unique=False,
    )
    op.create_index(
        "ix_message_links_chat_max",
        "message_links",
        ["chat_link_id", "max_message_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_message_links_chat_max", table_name="message_links")
    op.drop_index("ix_message_links_account_tg", table_name="message_links")
    op.drop_table("message_links")
    op.drop_table("max_users")
    op.drop_table("max_sessions")
    op.drop_index("ix_chat_links_account_topic", table_name="chat_links")
    op.drop_table("chat_links")
    op.drop_table("account_events")
    op.drop_table("accounts")
