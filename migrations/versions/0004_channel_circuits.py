"""Add canonical circuit groups and associate each channel with one circuit."""
from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

CIRCUITS = (
    ("integration", "Schaltkreis der Integration"),
    ("centering", "Schaltkreis der Zentrierung"),
    ("knowing", "Schaltkreis des Wissens"),
    ("ego", "Ego-Schaltkreis"),
    ("defense", "Schaltkreis der Verteidigung"),
    ("logic", "Schaltkreis der Logik"),
    ("sensing", "Schaltkreis der Wahrnehmung"),
)

# Frozen canonical assignments.
CIRCUIT_CHANNELS = {
    "integration": ((10, 20), (10, 57), (20, 34), (34, 57)),
    "centering": ((10, 34), (25, 51)),
    "knowing": ((1, 8), (2, 14), (3, 60), (12, 22), (20, 57),
                (23, 43), (24, 61), (28, 38), (39, 55)),
    "ego": ((19, 49), (21, 45), (26, 44), (32, 54), (37, 40)),
    "defense": ((6, 59), (27, 50)),
    "logic": ((4, 63), (5, 15), (7, 31), (9, 52), (16, 48), (17, 62), (18, 58)),
    "sensing": ((11, 56), (13, 33), (29, 46), (30, 41), (35, 36), (42, 53), (47, 64)),
}


def upgrade():
    circuits = op.create_table(
        "circuits",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.bulk_insert(circuits, [
        {"id": circuit_id, "name": name, "description": None}
        for circuit_id, name in CIRCUITS
    ])

    op.add_column("channels", sa.Column("circuit_id", sa.String(40), nullable=True))
    channels = sa.table(
        "channels",
        sa.column("gate_a", sa.Integer()),
        sa.column("gate_b", sa.Integer()),
        sa.column("circuit_id", sa.String(40)),
    )
    bind = op.get_bind()
    assigned = set()
    for circuit_id, pairs in CIRCUIT_CHANNELS.items():
        for gate_a, gate_b in pairs:
            pair = (min(gate_a, gate_b), max(gate_a, gate_b))
            if pair in assigned:
                raise RuntimeError(f"Duplicate circuit assignment: {pair}")
            assigned.add(pair)
            result = bind.execute(
                channels.update()
                .where(channels.c.gate_a == pair[0], channels.c.gate_b == pair[1])
                .values(circuit_id=circuit_id)
            )
            if result.rowcount != 1:
                raise RuntimeError(f"Expected one canonical channel for {pair}")
    if len(assigned) != 36 or bind.execute(
        sa.select(sa.func.count()).select_from(channels).where(channels.c.circuit_id.is_(None))
    ).scalar_one() != 0:
        raise RuntimeError("Circuit mapping must cover all 36 channels exactly once")

    with op.batch_alter_table("channels", recreate="always") as batch:
        batch.alter_column("circuit_id", existing_type=sa.String(40), nullable=False)
        batch.create_foreign_key(
            "fk_channel_circuit", "circuits", ["circuit_id"], ["id"], ondelete="RESTRICT",
        )


def downgrade():
    with op.batch_alter_table("channels", recreate="always") as batch:
        batch.drop_constraint("fk_channel_circuit", type_="foreignkey")
        batch.drop_column("circuit_id")
    op.drop_table("circuits")
