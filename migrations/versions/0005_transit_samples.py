"""Persistent versioned transit calculation cache."""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "transit_samples",
        sa.Column("namespace", sa.String(64), primary_key=True),
        sa.Column("timestamp_utc", sa.DateTime(), primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_transit_samples_created_at", "transit_samples", ["created_at"])


def downgrade():
    op.drop_index("ix_transit_samples_created_at", table_name="transit_samples")
    op.drop_table("transit_samples")
