"""One Telegram bot per Account; backfill existing encrypted bot tokens offline."""

from aiogram.utils.token import extract_bot_id
from alembic import op
from cryptography.fernet import Fernet
from sqlalchemy import BigInteger, CheckConstraint, Column, text

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def account_checks():
    # Alembic cannot carry unnamed reflected CHECK constraints across SQLite recreation.
    return (
        CheckConstraint("inbox_mode IN ('private', 'supergroup')"),
        CheckConstraint(
            "state IN ('new','logging_in','password_required','active',"
            "'paused','session_lost','error')"
        ),
    )


def upgrade():
    connection = op.get_bind()
    rows = connection.execute(text("SELECT id, tg_bot_token_enc FROM accounts")).all()
    ids = {}
    if rows:
        from maxgate.config import Settings

        key = op.get_context().config.attributes.get("secret_key")
        if key is None:
            key = Settings().secret_key
        crypto = Fernet(key.get_secret_value())
        try:
            ids = {
                row.id: extract_bot_id(crypto.decrypt(row.tg_bot_token_enc.encode()).decode())
                for row in rows
            }
        except Exception:
            raise ValueError("Cannot derive bot IDs from encrypted Account tokens") from None
        if len(set(ids.values())) != len(ids):
            raise ValueError(
                "Duplicate Telegram bots: resolve Account assignments before migration"
            )
    # Validate every legacy row before altering the schema.
    op.add_column("accounts", Column("tg_bot_id", BigInteger(), nullable=True))
    for account_id, bot_id in ids.items():
        connection.execute(
            text("UPDATE accounts SET tg_bot_id=:bot WHERE id=:id"),
            {"bot": bot_id, "id": account_id},
        )
    with op.batch_alter_table("accounts", table_args=account_checks()) as batch:
        batch.alter_column("tg_bot_id", existing_type=BigInteger(), nullable=False)
        batch.create_unique_constraint("uq_accounts_tg_bot_id", ["tg_bot_id"])


def downgrade():
    with op.batch_alter_table("accounts", table_args=account_checks()) as batch:
        batch.drop_constraint("uq_accounts_tg_bot_id", type_="unique")
        batch.drop_column("tg_bot_id")
