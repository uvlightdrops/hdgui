"""Persist center timeline detail preference."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("ui_settings") as batch:
        batch.add_column(
            sa.Column("center_timeline_samples", sa.Integer(), nullable=False, server_default="64"),
        )


def downgrade():
    with op.batch_alter_table("ui_settings") as batch:
        batch.drop_column("center_timeline_samples")
