import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import yaml
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.database import make_database
from app.hd_catalog import PACKAGED_PATH, export_entities, import_entities
from app.main import create_app
from app.models import BirthActivation, BirthChart, BirthProfile, HdEntity
from app.profiles import activation_key, chart_key


def upgrade(url: str, revision: str = "head") -> None:
    with patch.dict(os.environ, {"HDGUI_DATABASE_URL": url}):
        command.upgrade(Config("alembic.ini"), revision)


def downgrade(url: str, revision: str) -> None:
    with patch.dict(os.environ, {"HDGUI_DATABASE_URL": url}):
        command.downgrade(Config("alembic.ini"), revision)


class HdCatalogMigrationTests(unittest.TestCase):
    def test_revision_seeds_catalog_and_preserves_activations(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "migration.db"
            url = f"sqlite:///{path}"
            upgrade(url, "0006")
            with sqlite3.connect(path) as db:
                db.execute(
                    "INSERT INTO birth_profiles "
                    "(id, name, birth_local, timezone, birth_utc, place, chart_key) "
                    "VALUES ('p1', 'Profile', '2000-01-01 00:00:00', 'Etc/UTC', "
                    "'2000-01-01 00:00:00', 'Place', 'key')"
                )
                db.execute(
                    "INSERT INTO birth_activations "
                    "(profile_id, source, body, longitude, gate, line) "
                    "VALUES ('p1', 'design', 'Sun', 10, 1, 2)"
                )
            upgrade(url)
            engine, sessions = make_database(url)
            try:
                with sessions() as session:
                    counts = dict(session.execute(
                        select(HdEntity.category, func.count()).group_by(HdEntity.category)
                    ).all())
                    last_gate_line = session.get(HdEntity, "gate-line.64.6")
                    activation = session.scalar(select(BirthActivation))
                self.assertEqual(counts, {
                    "aura": 4, "authority": 8, "base": 5, "body": 13, "center": 9,
                    "color": 6, "cross-angle": 3, "cognition": 6, "definition": 5,
                    "direction": 2, "digestion": 6, "environment": 6, "gate-line": 384,
                    "line": 6, "motivation": 6, "not-self": 4, "perspective": 6,
                    "profile": 12, "signature": 4, "source": 2, "strategy": 4,
                    "tone": 6, "type": 5, "variable": 4,
                })
                self.assertEqual(last_gate_line.structure, {"gate": 64, "line": 6})
                self.assertIsNone(activation.color)
                self.assertIsNone(activation.tone)
                self.assertIsNone(activation.base)
                self.assertIsNone(activation.harmonic_gate)
                self.assertIn("birth_charts", inspect(engine).get_table_names())
                chart_fks = inspect(engine).get_foreign_keys("birth_charts")
                self.assertTrue(any(
                    fk["referred_table"] == "birth_profiles"
                    and fk["options"].get("ondelete") == "CASCADE"
                    for fk in chart_fks
                ))
                self.assertEqual(
                    sum(fk["referred_table"] == "hd_entities" for fk in chart_fks), 5,
                )
                with self.assertRaises(IntegrityError), engine.begin() as connection:
                    connection.execute(text(
                        "INSERT INTO birth_activations "
                        "(profile_id, source, body, longitude, gate, line, color) "
                        "VALUES ('p1', 'personality', 'Moon', 20, 2, 1, 7)"
                    ))
            finally:
                engine.dispose()

    def test_revision_downgrades_and_reapplies(self):
        with tempfile.TemporaryDirectory() as temp:
            url = f"sqlite:///{Path(temp) / 'roundtrip.db'}"
            upgrade(url)
            downgrade(url, "0006")
            engine, _ = make_database(url)
            try:
                tables = inspect(engine).get_table_names()
                self.assertNotIn("hd_entities", tables)
                self.assertNotIn("birth_charts", tables)
                columns = {column["name"] for column in inspect(engine).get_columns("birth_activations")}
                self.assertNotIn("color", columns)
            finally:
                engine.dispose()
            upgrade(url)
            engine, sessions = make_database(url)
            try:
                with sessions() as session:
                    self.assertEqual(session.scalar(
                        select(func.count()).select_from(HdEntity).where(HdEntity.category == "gate-line")
                    ), 384)
            finally:
                engine.dispose()

    def test_packaged_texts_import_export_and_render(self):
        with tempfile.TemporaryDirectory() as temp:
            url = f"sqlite:///{Path(temp) / 'catalog.db'}"
            upgrade(url)
            engine, sessions = make_database(url)
            try:
                imported = import_entities(sessions, PACKAGED_PATH)
                self.assertEqual(imported, 132)
                with sessions() as session:
                    entity = session.get(HdEntity, "type.generator")
                    self.assertEqual(entity.description_model, "unbekannt")
                    empty_slots = session.scalar(
                        select(func.count()).select_from(HdEntity).where(
                            HdEntity.category == "gate-line",
                            HdEntity.description.is_(None),
                        )
                    )
                self.assertEqual(entity.description_origin, "ai")
                self.assertEqual(empty_slots, 384)

                exported = Path(temp) / "export.yaml"
                export_entities(sessions, exported)
                with sessions() as session:
                    total = session.scalar(select(func.count()).select_from(HdEntity))
                self.assertEqual(import_entities(sessions, exported), total)

                app = create_app(Settings(database_url=url, session_secret="test-only"))
                with TestClient(app) as client:
                    page = client.get("/knowledge/entities?category=gate-line")
                    self.assertEqual(page.status_code, 200)
                    self.assertEqual(page.text.count("<article "), 384)
                    self.assertEqual(client.get("/knowledge/entities?category=unknown").status_code, 404)
            finally:
                engine.dispose()

    def test_import_protects_human_text_until_explicit_override(self):
        with tempfile.TemporaryDirectory() as temp:
            url = f"sqlite:///{Path(temp) / 'provenance.db'}"
            upgrade(url)
            engine, sessions = make_database(url)
            try:
                fixture_path = Path(temp) / "text.yaml"

                def write_fixture(origin: str, description: str) -> None:
                    provenance = {
                        "origin": origin,
                        "author": "Test",
                        "model": None if origin == "human" else "unbekannt",
                        "written_at": "2026-11-14",
                    }
                    fixture_path.write_text(yaml.safe_dump({
                        "schema_version": 1,
                        "entities": [{
                            "id": "type.generator",
                            "name": "Generator",
                            "description": description,
                            "provenance": provenance,
                        }],
                    }, allow_unicode=True), encoding="utf-8")

                write_fixture("human", "Eigener Text")
                import_entities(sessions, fixture_path)
                write_fixture("ai", "Neuer Entwurf")
                with self.assertRaises(ValueError):
                    import_entities(sessions, fixture_path)
                with sessions() as session:
                    self.assertEqual(session.get(HdEntity, "type.generator").description, "Eigener Text")
                import_entities(sessions, fixture_path, replace_human=True)
                with sessions() as session:
                    entity = session.get(HdEntity, "type.generator")
                    self.assertEqual(entity.description, "Neuer Entwurf")
                    self.assertEqual(entity.description_origin, "ai")
            finally:
                engine.dispose()

    def test_profile_chart_shows_catalog_properties_beside_bodygraph(self):
        with tempfile.TemporaryDirectory() as temp:
            url = f"sqlite:///{Path(temp) / 'profile-chart.db'}"
            upgrade(url)
            settings = Settings(database_url=url, session_secret="test-only")
            engine, sessions = make_database(url)
            try:
                import_entities(sessions, PACKAGED_PATH)
                birth_utc = datetime(2000, 1, 1, 12)
                profile_id = "profile-test"
                cross_id = "cross.right.1-2-3-4"
                with sessions.begin() as session:
                    session.add(HdEntity(
                        id=cross_id, category="cross",
                        structure={"angle": "right", "gates": [1, 2, 3, 4]},
                        name="Rechtes Beispielkreuz", description_origin="unwritten",
                        review_status="draft",
                    ))
                    profile = BirthProfile(
                        id=profile_id, name="Testprofil", birth_local=birth_utc,
                        timezone="Etc/UTC", birth_utc=birth_utc, place="Testort",
                        chart_key=chart_key(birth_utc, settings.api_url, settings.engine_version),
                        chart_svg="<svg xmlns='http://www.w3.org/2000/svg'/>",
                        chart_generated_at=birth_utc,
                        activation_key=activation_key(birth_utc, settings.api_url, settings.engine_version),
                        activation_engine_version=settings.engine_version,
                        activations_calculated_at=birth_utc,
                    )
                    profile.activations.extend([
                        BirthActivation(source="personality", body="Sun", longitude=1, gate=1, line=1),
                        BirthActivation(source="design", body="Sun", longitude=2, gate=3, line=2),
                    ])
                    session.add(profile)
                    links = {
                        "energy_type": "type.generator",
                        "strategy": "strategy.respond",
                        "inner_authority": "authority.sacral",
                        "profile": "profile.1-3",
                        "definition": "definition.single",
                        "incarnation_cross": cross_id,
                        "signature": "signature.satisfaction",
                        "not_self": "not-self.frustration",
                        "aura": "aura.enveloping",
                        **{
                            f"variables.{slot}.direction": "direction.left"
                            for slot in ("top_left", "bottom_left", "bottom_right", "top_right")
                        },
                    }
                    session.add(BirthChart(
                        profile_id=profile_id, schema_version=1, calculation_key=profile.activation_key,
                        engine_version=settings.engine_version, api_endpoint="/calculate",
                        calculated_at=birth_utc, birth_utc=birth_utc, design_utc=datetime(1999, 10, 1),
                        type_id="type.generator", authority_id="authority.sacral",
                        hd_profile_id="profile.1-3", definition_id="definition.single",
                        incarnation_cross_id=cross_id,
                        general={
                            "energy_type": "Generator", "strategy": "Wait to Respond",
                            "inner_authority": "Sacral Authority", "profile": "1/3",
                            "definition": "Single Definition", "inc_cross": "Example Cross",
                            "signature": "Satisfaction", "not_self": "Frustration",
                            "aura": "Open & Enveloping",
                        },
                        variables={
                            slot: {
                                "value": "left", "name": f"Variable {slot}",
                                "aspect": "aspect", "def_type": "type",
                            }
                            for slot in ("top_left", "bottom_left", "bottom_right", "top_right")
                        },
                        centers={"defined": ["Sacral"], "undefined": [], "open": []},
                        channels=[], catalog_links=links, unmapped_values={}, raw_response={},
                    ))
                app = create_app(settings)
                with TestClient(app) as client:
                    response = client.get(f"/profiles/{profile_id}/chart")
                self.assertEqual(response.status_code, 200)
                self.assertIn("profile-chart-layout", response.text)
                self.assertIn("profile-chart-large", response.text)
                self.assertIn("Typ", response.text)
                self.assertIn("Generator", response.text)
                self.assertIn("Reagieren", response.text)
                self.assertIn("Innere Autorität", response.text)
                self.assertIn("Profil 1/3", response.text)
                self.assertIn("Rechtes Beispielkreuz", response.text)
                self.assertIn('category=direction&amp;entity=direction.left', response.text)
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
