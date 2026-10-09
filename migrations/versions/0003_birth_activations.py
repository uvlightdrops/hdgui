"""Canonical gates/channels, current birth profile and persisted natal activations.

The gate/channel topology below is a frozen snapshot of ``app.bodygraph``
(CHANNELS / GATE_CENTERS) at the time of this revision; tests verify that both
agree. Fresh databases receive generic gate names and no description texts.
Private texts are imported explicitly from a local fixture after migration.
This seed change does not alter the deployed schema, topology or existing data.
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

CENTERS = ("Head", "Ajna", "Throat", "G", "Heart", "Spleen", "Solar", "Sacral", "Root")
GATE_CENTERS = {
    "Head": (64, 61, 63),
    "Ajna": (47, 24, 4, 17, 43, 11),
    "Throat": (62, 23, 56, 35, 12, 45, 33, 8, 31, 20, 16),
    "G": (1, 13, 25, 46, 2, 15, 10, 7),
    "Heart": (21, 51, 26, 40),
    "Spleen": (48, 57, 44, 50, 32, 28, 18),
    "Solar": (6, 37, 22, 36, 30, 55, 49),
    "Sacral": (5, 14, 29, 59, 9, 3, 42, 27, 34),
    "Root": (53, 60, 52, 19, 39, 41, 58, 38, 54),
}
CHANNELS = (
    (64, 47), (61, 24), (63, 4), (17, 62), (43, 23), (11, 56),
    (31, 7), (8, 1), (33, 13), (20, 10), (16, 48), (20, 57),
    (20, 34), (12, 22), (35, 36), (45, 21), (25, 51), (10, 57),
    (10, 34), (2, 14), (15, 5), (46, 29), (44, 26), (37, 40),
    (27, 50), (34, 57), (59, 6), (42, 53), (3, 60), (9, 52),
    (32, 54), (28, 38), (18, 58), (19, 49), (39, 55), (41, 30),
)
def upgrade():
    gate_center = {gate: center for center, members in GATE_CENTERS.items() for gate in members}
    assert set(gate_center) == set(range(1, 65)) and len(CHANNELS) == 36
    gates = op.create_table(
        "gates",
        sa.Column("number", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("center", sa.String(10), nullable=False),
        sa.CheckConstraint("number BETWEEN 1 AND 64", name="ck_gate_number"),
        sa.CheckConstraint("center IN ('" + "', '".join(CENTERS) + "')", name="ck_gate_center"),
    )
    channels = op.create_table(
        "channels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("gate_a", sa.Integer(), nullable=False),
        sa.Column("gate_b", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["gate_a"], ["gates.number"], name="fk_channel_gate_a", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["gate_b"], ["gates.number"], name="fk_channel_gate_b", ondelete="RESTRICT"),
        sa.UniqueConstraint("gate_a", "gate_b", name="uq_channel_gates"),
        sa.CheckConstraint("gate_a < gate_b", name="ck_channel_unordered"),
    )
    op.bulk_insert(gates, [
        {"number": number, "name": f"Gate {number}", "description": None,
         "center": gate_center[number]}
        for number in range(1, 65)
    ])
    rows = []
    for index, pair in enumerate(CHANNELS, start=1):
        rows.append({"id": index, "gate_a": min(pair), "gate_b": max(pair),
                     "name": None, "description": None})
    op.bulk_insert(channels, rows)

    with op.batch_alter_table("birth_profiles") as batch:
        batch.add_column(sa.Column("activation_key", sa.String(64), nullable=True))
        batch.add_column(sa.Column("activation_engine_version", sa.String(64), nullable=True))
        batch.add_column(sa.Column("activations_calculated_at", sa.DateTime(), nullable=True))
    op.create_table(
        "birth_activations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("profile_id", sa.String(36), nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("body", sa.String(40), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("gate", sa.Integer(), nullable=False),
        sa.Column("line", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["birth_profiles.id"],
                                name="fk_birth_activation_profile", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["gate"], ["gates.number"],
                                name="fk_birth_activation_gate", ondelete="RESTRICT"),
        sa.UniqueConstraint("profile_id", "source", "body", name="uq_birth_activation_body"),
        sa.CheckConstraint("source IN ('personality', 'design')", name="ck_birth_activation_source"),
        sa.CheckConstraint("gate BETWEEN 1 AND 64", name="ck_birth_activation_gate"),
        sa.CheckConstraint("line BETWEEN 1 AND 6", name="ck_birth_activation_line"),
        sa.CheckConstraint("longitude >= 0 AND longitude < 360", name="ck_birth_activation_longitude"),
    )
    op.create_index("ix_birth_activations_profile_id", "birth_activations", ["profile_id"])
    # SQLite cannot add a foreign key in place; batch mode recreates ui_settings
    # and copies the existing singleton row.
    with op.batch_alter_table("ui_settings", recreate="always") as batch:
        batch.add_column(sa.Column("active_profile_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_ui_settings_active_profile", "birth_profiles",
                                 ["active_profile_id"], ["id"], ondelete="SET NULL")


def downgrade():
    with op.batch_alter_table("ui_settings", recreate="always") as batch:
        batch.drop_constraint("fk_ui_settings_active_profile", type_="foreignkey")
        batch.drop_column("active_profile_id")
    op.drop_index("ix_birth_activations_profile_id", table_name="birth_activations")
    op.drop_table("birth_activations")
    with op.batch_alter_table("birth_profiles") as batch:
        batch.drop_column("activations_calculated_at")
        batch.drop_column("activation_engine_version")
        batch.drop_column("activation_key")
    op.drop_table("channels")
    op.drop_table("gates")
