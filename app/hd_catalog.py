"""Replaceable HD entity texts, with per-record authorship and safe explicit import."""
import argparse
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import Field, StringConstraints, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import SQLAlchemyError

from app.catalog_fixture import FixtureRecord, UniqueKeyLoader
from app.config import Settings
from app.database import make_database
from app.models import HdEntity

PACKAGED_PATH = Path(__file__).parent / "data" / "hd-entities.yaml"
CATEGORY_LABELS = {
    "type": "Typen", "strategy": "Strategien", "signature": "Signaturen",
    "not-self": "Nicht-Selbst-Themen", "aura": "Aura", "authority": "Autoritäten",
    "profile": "Profile", "definition": "Definition", "center": "Zentren",
    "body": "Himmelskörper", "source": "Persönlichkeit und Design", "line": "Linien",
    "gate-line": "Torlinien – Textplätze", "cross-angle": "Kreuz-Geometrie",
    "cross": "Erfasste Inkarnationskreuze", "variable": "Variablen",
    "direction": "Pfeilrichtungen", "color": "Color", "tone": "Tone", "base": "Base",
    "digestion": "Determination / Verdauung", "environment": "Umgebung",
    "perspective": "Perspektive", "motivation": "Motivation", "cognition": "Kognition",
}
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class Provenance(FixtureRecord):
    origin: Literal["ai", "human"]
    author: Name
    model: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = None
    written_at: date
    status: Literal["draft", "reviewed"] = "draft"

    @field_validator("written_at", mode="before")
    @classmethod
    def parse_date(cls, value: date | str) -> date:
        return date.fromisoformat(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def check_model(self) -> "Provenance":
        if self.origin == "ai" and not self.model:
            raise ValueError("KI-Texte benötigen eine Modellangabe (oder ausdrücklich 'unbekannt').")
        if self.origin == "human" and self.model is not None:
            raise ValueError("Eigene Texte dürfen nicht als KI-Modelltext ausgezeichnet sein.")
        return self


class CrossStructure(FixtureRecord):
    angle: Literal["right", "juxtaposition", "left"]
    gates: Annotated[list[Annotated[int, Field(ge=1, le=64)]], Field(min_length=4, max_length=4)]

    def entity_id(self) -> str:
        return f"cross.{self.angle}." + "-".join(map(str, self.gates))


class EntityText(FixtureRecord):
    id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,80}$")]
    name: Name
    description: Text | None
    provenance: Provenance | None
    structure: dict | None = None

    @model_validator(mode="after")
    def check_authorship(self) -> "EntityText":
        if (self.description is None) != (self.provenance is None):
            raise ValueError("Jeder Beschreibungstext benötigt Herkunft; leere Textplätze nicht.")
        return self


class EntityFixture(FixtureRecord):
    schema_version: Literal[1]
    entities: Annotated[list[EntityText], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_entities(self) -> "EntityFixture":
        if len({record.id for record in self.entities}) != len(self.entities):
            raise ValueError("Doppelte Entitäten-Kennung im Katalog.")
        return self


def ensure_cross(session: Session, structure: CrossStructure, name: str) -> HdEntity:
    """Register a factual four-gate combination without inventing an interpretation."""
    entity = session.get(HdEntity, structure.entity_id())
    if entity is None:
        entity = HdEntity(
            id=structure.entity_id(), category="cross", structure=structure.model_dump(), name=name,
        )
        session.add(entity)
        session.flush()
    elif entity.category != "cross" or entity.structure != structure.model_dump():
        raise ValueError("Inkarnationskreuz-Kennung und gespeicherte Struktur widersprechen sich.")
    return entity


def apply_text(session: Session, record: EntityText, replace_human: bool) -> None:
    entity = session.get(HdEntity, record.id)
    if entity is None:
        if not record.id.startswith("cross."):
            raise ValueError(f"Unbekannte Entität {record.id}; Migrationen prüfen.")
        cross = CrossStructure.model_validate(record.structure)
        if cross.entity_id() != record.id:
            raise ValueError("Kreuz-Kennung muss Winkel und Tore in P-Sonne/P-Erde/D-Sonne/D-Erde-Reihenfolge enthalten.")
        entity = ensure_cross(session, cross, record.name)
    if record.structure is not None and record.structure != entity.structure:
        raise ValueError(f"{record.id}: Import darf die feste Struktur nicht verändern.")
    incoming_origin = record.provenance.origin if record.provenance else "unwritten"
    if entity.description_origin == "human" and incoming_origin != "human" and not replace_human:
        raise ValueError(f"{record.id}: Eigener Text geschützt; Ersetzung nur mit --replace-human.")
    entity.name, entity.description = record.name, record.description
    entity.description_origin = incoming_origin
    entity.description_author = record.provenance.author if record.provenance else None
    entity.description_model = record.provenance.model if record.provenance else None
    entity.description_date = record.provenance.written_at.isoformat() if record.provenance else None
    entity.review_status = record.provenance.status if record.provenance else "draft"


def import_entities(
    sessions: sessionmaker, path: Path = PACKAGED_PATH, *, replace_human: bool = False,
) -> int:
    """Apply a complete or partial text fixture atomically, without deleting other entries."""
    with Path(path).open(encoding="utf-8-sig") as source:
        try:
            fixture = EntityFixture.model_validate(yaml.load(source, Loader=UniqueKeyLoader))
        except yaml.YAMLError as exc:
            raise ValueError(f"Ungültiges YAML: {exc}") from exc
    with sessions.begin() as session:
        for record in fixture.entities:
            apply_text(session, record, replace_human)
    return len(fixture.entities)


def export_entities(sessions: sessionmaker, path: Path) -> None:
    """Export editable records without overwriting an existing local file."""
    with sessions() as session:
        records = []
        for entity in session.scalars(select(HdEntity).order_by(HdEntity.id)):
            provenance = None
            if entity.description_origin != "unwritten":
                provenance = {
                    "origin": entity.description_origin, "author": entity.description_author,
                    "model": entity.description_model, "written_at": entity.description_date,
                    "status": entity.review_status,
                }
            records.append({
                "id": entity.id, "name": entity.name, "description": entity.description,
                "structure": entity.structure, "provenance": provenance,
            })
    fixture = EntityFixture.model_validate({"schema_version": 1, "entities": records})
    with path.open("x", encoding="utf-8") as target:
        target.write("# Text-Herkunft: siehe provenance pro Eintrag; KI- und eigene Texte sind getrennt gekennzeichnet.\n")
        yaml.safe_dump(fixture.model_dump(mode="json"), target, allow_unicode=True, sort_keys=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    importing = commands.add_parser("import")
    importing.add_argument("path", nargs="?", type=Path, default=PACKAGED_PATH)
    importing.add_argument("--replace-human", action="store_true")
    exporting = commands.add_parser("export")
    exporting.add_argument("path", type=Path)
    args = parser.parse_args()
    engine, sessions = make_database(Settings().database_url)
    try:
        if args.command == "import":
            count = import_entities(sessions, args.path, replace_human=args.replace_human)
            print(f"{count} Entitätentexte importiert.")
        else:
            export_entities(sessions, args.path)
            print(f"Entitäten exportiert nach {args.path}.")
    except (OSError, ValueError, SQLAlchemyError) as exc:
        parser.exit(1, f"Entitätenkatalog fehlgeschlagen (Datenbank/Migrationen prüfen): {exc}\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
