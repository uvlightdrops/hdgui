"""Persistent transit display settings."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table(
        "ui_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cache_seconds", sa.Integer(), nullable=False),
        sa.Column("autoreload", sa.Boolean(), nullable=False),
    )
    op.bulk_insert(table, [{"id": 1, "cache_seconds": 60, "autoreload": True}])


def downgrade():
    op.drop_table("ui_settings")
