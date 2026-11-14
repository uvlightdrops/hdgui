"""Create stable knowledge identities and localized text records."""
from pathlib import Path

import yaml
from alembic import context, op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

CELESTIAL_FIXTURE = Path(__file__).parents[2] / "app" / "data" / "celestial-bodies.yaml"
TEXT_KIND = "human-design"
LOCALE = "de"


def _read_celestial_fixture():
    with CELESTIAL_FIXTURE.open(encoding="utf-8") as source:
        fixture = yaml.safe_load(source)
    if not isinstance(fixture, dict) or fixture.get("schema_version") != 1:
        raise RuntimeError("The celestial-body fixture has an unsupported schema version.")
    records = fixture.get("entities")
    if not isinstance(records, list) or len(records) != 13:
        raise RuntimeError("The celestial-body fixture must contain all 13 known bodies.")
    ids = [record.get("id") for record in records if isinstance(record, dict)]
    if len(ids) != 13 or len(set(ids)) != 13:
        raise RuntimeError("The celestial-body fixture must contain unique entity IDs.")
    return records


def upgrade():
    if context.is_offline_mode():
        raise RuntimeError("Revision 0008 copies existing database records and requires online mode.")
    op.add_column("ui_settings", sa.Column("catalog_locale", sa.String(35), nullable=True))
    op.create_table(
        "knowledge_entities",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("domain", sa.String(30), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("structure", sa.JSON(), nullable=False),
    )
    op.create_index("ix_knowledge_entities_domain", "knowledge_entities", ["domain"])
    op.create_index("ix_knowledge_entities_category", "knowledge_entities", ["category"])
    op.create_table(
        "knowledge_entity_texts",
        sa.Column(
            "entity_id", sa.String(100),
            sa.ForeignKey("knowledge_entities.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("locale", sa.String(35), primary_key=True),
        sa.Column("text_kind", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(200)),
        sa.Column("description", sa.Text()),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("origin", sa.String(12), nullable=False),
        sa.Column("author", sa.String(200)),
        sa.Column("model", sa.String(100)),
        sa.Column("authored_at", sa.String(10)),
        sa.Column("review_status", sa.String(12), nullable=False),
        sa.CheckConstraint(
            "origin IN ('legacy', 'unwritten', 'ai', 'human')",
            name="ck_knowledge_text_origin",
        ),
        sa.CheckConstraint(
            "review_status IN ('unknown', 'draft', 'reviewed')",
            name="ck_knowledge_text_review",
        ),
    )

    bind = op.get_bind()
    entities = sa.table(
        "knowledge_entities",
        sa.column("id", sa.String(100)),
        sa.column("domain", sa.String(30)),
        sa.column("category", sa.String(40)),
        sa.column("structure", sa.JSON()),
    )
    texts = sa.table(
        "knowledge_entity_texts",
        sa.column("entity_id", sa.String(100)),
        sa.column("locale", sa.String(35)),
        sa.column("text_kind", sa.String(40)),
        sa.column("name", sa.String(200)),
        sa.column("description", sa.Text()),
        sa.column("metadata", sa.JSON()),
        sa.column("origin", sa.String(12)),
        sa.column("author", sa.String(200)),
        sa.column("model", sa.String(100)),
        sa.column("authored_at", sa.String(10)),
        sa.column("review_status", sa.String(12)),
    )
    entity_rows = []
    text_rows = []

    for row in bind.execute(sa.text("SELECT number, name, description, center FROM gates")):
        entity_id = f"gate.{row.number}"
        entity_rows.append({
            "id": entity_id, "domain": "human-design", "category": "gate",
            "structure": {"number": row.number, "center": row.center},
        })
        text_rows.append(_legacy_text(entity_id, row.name, row.description))

    for row in bind.execute(sa.text(
        "SELECT id, gate_a, gate_b, circuit_id, name, description FROM channels"
    )):
        entity_id = f"channel.{row.gate_a}-{row.gate_b}"
        entity_rows.append({
            "id": entity_id, "domain": "human-design", "category": "channel",
            "structure": {
                "gates": [row.gate_a, row.gate_b], "circuit": row.circuit_id,
            },
        })
        text_rows.append(_legacy_text(entity_id, row.name, row.description))

    for row in bind.execute(sa.text("SELECT id, name, description FROM circuits")):
        entity_id = f"circuit.{row.id}"
        entity_rows.append({
            "id": entity_id, "domain": "human-design", "category": "circuit",
            "structure": {},
        })
        text_rows.append(_legacy_text(entity_id, row.name, row.description))

    for row in bind.execute(sa.text(
        "SELECT id, category, structure, name, description, description_origin, "
        "description_author, description_model, description_date, review_status FROM hd_entities"
    )):
        entity_rows.append({
            "id": row.id,
            "domain": "shared" if row.category == "body" else "human-design",
            "category": row.category,
            "structure": row.structure or {},
        })
        text_rows.append({
            **_legacy_text(row.id, row.name, row.description),
            "origin": row.description_origin,
            "author": row.description_author,
            "model": row.description_model,
            "authored_at": row.description_date,
            "review_status": row.review_status if row.description_origin != "unwritten" else "unknown",
        })

    if sum(row["category"] == "gate" for row in entity_rows) != 64:
        raise RuntimeError("Expected 64 canonical gates when creating the unified catalog.")
    if sum(row["category"] == "channel" for row in entity_rows) != 36:
        raise RuntimeError("Expected 36 canonical channels when creating the unified catalog.")
    if sum(row["category"] == "circuit" for row in entity_rows) != 7:
        raise RuntimeError("Expected 7 canonical circuits when creating the unified catalog.")

    for row in _read_celestial_fixture():
        entity_id = row["id"]
        if entity_id not in {entity["id"] for entity in entity_rows}:
            raise RuntimeError(f"Celestial catalog identity is missing: {entity_id}")
        text_rows.append({
            "entity_id": entity_id,
            "locale": LOCALE,
            "text_kind": "astrology-symbolism",
            "name": row["name"],
            "description": row["description"],
            "metadata": row["metadata"],
            "origin": "ai",
            "author": "GitHub Copilot",
            "model": "unbekannt",
            "authored_at": "2026-10-25",
            "review_status": "draft",
        })

    bind.execute(entities.insert(), entity_rows)
    bind.execute(texts.insert(), text_rows)


def _legacy_text(entity_id, name, description):
    return {
        "entity_id": entity_id,
        "locale": LOCALE,
        "text_kind": TEXT_KIND,
        "name": name,
        "description": description,
        "metadata": {},
        "origin": "legacy",
        "author": None,
        "model": None,
        "authored_at": None,
        "review_status": "unknown",
    }


def downgrade():
    op.drop_table("knowledge_entity_texts")
    op.drop_index("ix_knowledge_entities_category", table_name="knowledge_entities")
    op.drop_index("ix_knowledge_entities_domain", table_name="knowledge_entities")
    op.drop_table("knowledge_entities")
    op.drop_column("ui_settings", "catalog_locale")
