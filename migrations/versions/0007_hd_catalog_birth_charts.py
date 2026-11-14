"""Individual HD catalog and complete natal snapshots; no interpretation texts."""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# Frozen structural vocabulary. Editorial names/descriptions are imported explicitly.
VOCABULARY = {
    "type": ("generator", "manifesting-generator", "projector", "manifestor", "reflector"),
    "strategy": ("respond", "invitation", "inform", "lunar-cycle"),
    "signature": ("satisfaction", "success", "peace", "surprise"),
    "not-self": ("frustration", "bitterness", "anger", "disappointment"),
    "aura": ("enveloping", "focused", "closed", "sampling"),
    "authority": ("emotional", "sacral", "splenic", "ego-manifested", "ego-projected",
                  "self-projected", "environmental", "lunar"),
    "definition": ("none", "single", "split", "triple-split", "quadruple-split"),
    "center": ("Head", "Ajna", "Throat", "G", "Heart", "Spleen", "Solar", "Sacral", "Root"),
    "body": ("Sun", "Earth", "Moon", "North_Node", "South_Node", "Mercury", "Venus",
             "Mars", "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto"),
    "source": ("personality", "design"),
    "variable": ("digestion", "environment", "perspective", "motivation"),
    "direction": ("left", "right"),
    "cross-angle": ("right", "juxtaposition", "left"),
    "digestion": ("appetite", "taste", "thirst", "touch", "sound", "light"),
    "environment": ("caves", "markets", "kitchens", "mountains", "valleys", "shores"),
    "perspective": ("survival", "possibility", "power", "wanting", "probability", "personal"),
    "motivation": ("fear", "hope", "desire", "need", "guilt", "innocence"),
    "cognition": ("smell", "taste", "outer-vision", "inner-vision", "feeling", "touch"),
}
PROFILES = ("1/3", "1/4", "2/4", "2/5", "3/5", "3/6", "4/6", "4/1", "5/1", "5/2", "6/2", "6/3")


def seed_entities():
    rows = []

    def add(category, code, structure=None, name=None):
        rows.append({
            "id": f"{category}.{code}", "category": category, "structure": structure or {},
            "name": name or code, "description_origin": "unwritten", "review_status": "draft",
        })

    for category, codes in VOCABULARY.items():
        for index, code in enumerate(codes, start=1):
            structure = {"number": index} if category in (
                "digestion", "environment", "perspective", "motivation", "cognition",
            ) else {}
            add(category, code, structure)
    for category, count in (("line", 6), ("color", 6), ("tone", 6), ("base", 5)):
        for number in range(1, count + 1):
            add(category, str(number), {"number": number})
    for code in PROFILES:
        personality, design = map(int, code.split("/"))
        add("profile", code.replace("/", "-"), {"personality_line": personality, "design_line": design}, code)
    for gate in range(1, 65):
        for line in range(1, 7):
            add("gate-line", f"{gate}.{line}", {"gate": gate, "line": line}, f"Tor {gate}, Linie {line}")
    return rows


def upgrade():
    entities = op.create_table(
        "hd_entities",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("structure", sa.JSON(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("description_origin", sa.String(12), nullable=False),
        sa.Column("description_author", sa.String(200)),
        sa.Column("description_model", sa.String(100)),
        sa.Column("description_date", sa.String(10)),
        sa.Column("review_status", sa.String(12), nullable=False),
        sa.CheckConstraint("description_origin IN ('unwritten', 'ai', 'human')", name="ck_hd_text_origin"),
        sa.CheckConstraint("review_status IN ('draft', 'reviewed')", name="ck_hd_text_review"),
    )
    op.create_index("ix_hd_entities_category", "hd_entities", ["category"])
    op.bulk_insert(entities, seed_entities())
    with op.batch_alter_table("birth_activations") as batch:
        for column in ("color", "tone", "base", "harmonic_gate"):
            batch.add_column(sa.Column(column, sa.Integer(), nullable=True))
        for column, upper in (("color", 6), ("tone", 6), ("base", 5)):
            batch.create_check_constraint(f"ck_birth_activation_{column}", f"{column} BETWEEN 1 AND {upper}")
        batch.create_foreign_key("fk_activation_harmonic_gate", "gates", ["harmonic_gate"], ["number"],
                                 ondelete="RESTRICT")
    op.create_table(
        "birth_charts",
        sa.Column("profile_id", sa.String(36), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("calculation_key", sa.String(64), nullable=False),
        sa.Column("engine_version", sa.String(64), nullable=False),
        sa.Column("api_endpoint", sa.String(40), nullable=False),
        sa.Column("calculated_at", sa.DateTime(), nullable=False),
        sa.Column("birth_utc", sa.DateTime(), nullable=False),
        sa.Column("design_utc", sa.DateTime(), nullable=False),
        *[sa.Column(name, sa.String(80), sa.ForeignKey("hd_entities.id", ondelete="RESTRICT"),
                    nullable=name not in ("hd_profile_id", "incarnation_cross_id"))
          for name in ("type_id", "authority_id", "hd_profile_id", "definition_id", "incarnation_cross_id")],
        *[sa.Column(name, sa.JSON(), nullable=False)
          for name in ("general", "variables", "centers", "channels", "catalog_links", "unmapped_values",
                       "raw_response")],
        sa.ForeignKeyConstraint(["profile_id"], ["birth_profiles.id"], name="fk_birth_chart_profile",
                                ondelete="CASCADE"),
        sa.CheckConstraint("schema_version = 1", name="ck_birth_chart_schema"),
    )


def downgrade():
    op.drop_table("birth_charts")
    with op.batch_alter_table("birth_activations") as batch:
        batch.drop_constraint("fk_activation_harmonic_gate", type_="foreignkey")
        for column in ("color", "tone", "base"):
            batch.drop_constraint(f"ck_birth_activation_{column}", type_="check")
        for column in ("color", "tone", "base", "harmonic_gate"):
            batch.drop_column(column)
    op.drop_index("ix_hd_entities_category", table_name="hd_entities")
    op.drop_table("hd_entities")
