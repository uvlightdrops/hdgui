"""Persistent birth profiles with cached SVG charts."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "birth_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("birth_local", sa.DateTime(), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("birth_utc", sa.DateTime(), nullable=False),
        sa.Column("place", sa.String(200), nullable=False),
        sa.Column("chart_key", sa.String(64), nullable=False),
        sa.Column("chart_svg", sa.Text(), nullable=True),
        sa.Column("chart_generated_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table("birth_profiles")
