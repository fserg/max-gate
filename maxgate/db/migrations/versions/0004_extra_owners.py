"""Additional Owner Telegram ids per Account."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "accounts",
        sa.Column("extra_owner_tg_user_ids", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade():
    op.drop_column("accounts", "extra_owner_tg_user_ids")
