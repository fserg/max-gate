"""Persist Telegram Topic titles independently of MAX chat names."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("chat_links", sa.Column("topic_title", sa.String(), nullable=True))


def downgrade():
    op.drop_column("chat_links", "topic_title")
