import os
import re
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.catalog_fixture import DEFAULT_PATH, PACKAGED_DEFAULT_PATH, import_fixture
from app.config import Settings
from app.database import make_database
from app.gate_descriptions import CatalogMissing, gate_descriptions, gate_tooltip
from app.main import create_app
from app.models import BirthActivation, BirthProfile, Channel, Circuit, Gate, UiSettings
from app.transit import TransitFrame, TransitPosition
from catalog_fixture_helpers import fixture_catalog, write_fixture


class GateDescriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=".")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "fixture.yaml"
        self.url = f"sqlite:///{Path(self.temp.name) / 'catalog.db'}"
        with patch.dict(os.environ, {"HDGUI_DATABASE_URL": self.url}):
            command.upgrade(Config("alembic.ini"), "head")
        self.engine, self.sessions = make_database(self.url)
        self.addCleanup(self.engine.dispose)
        write_fixture(self.path)

    def test_fresh_migration_without_fixture_and_explicit_missing_schema(self):
        with patch("pathlib.Path.open", side_effect=AssertionError("No fixture reads")), self.sessions() as session:
            descriptions = gate_descriptions(session)
        self.assertEqual(set(descriptions), set(range(1, 65)))
        self.assertEqual(descriptions[41]["name"], "Gate 41")
        self.assertIn("noch keine Beschreibung", gate_tooltip(41, descriptions))
        with patch.dict(os.environ, {"HDGUI_DATABASE_URL": f"sqlite:///{Path(self.temp.name) / 'fresh.db'}"}):
            # Migration may read its own source/config, but not catalog fixtures.
            original = Path.open

            def no_fixture(path, *args, **kwargs):
                if path.suffix in (".csv", ".yaml"):
                    raise AssertionError("Migration must not read fixtures")
                return original(path, *args, **kwargs)

            with patch.object(Path, "open", no_fixture):
                command.upgrade(Config("alembic.ini"), "head")
        engine, sessions = make_database(f"sqlite:///{Path(self.temp.name) / 'missing.db'}")
        try:
            with sessions() as session, self.assertRaises(CatalogMissing):
                gate_descriptions(session)
        finally:
            engine.dispose()

    def test_import_is_idempotent_preserves_profiles_and_imports_prepared_text(self):
        with self.sessions.begin() as session:
            session.add(BirthProfile(id="p", name="Sample profile", birth_local=datetime(2000, 1, 1),
                                     timezone="Etc/UTC", birth_utc=datetime(2000, 1, 1), place="Here",
                                     chart_key="svg-key", chart_svg="<svg/>", activation_key="activation-key",
                                     activation_engine_version="sample"))
            session.flush()
            session.add(BirthActivation(profile_id="p", source="design", body="Sun",
                                        longitude=1, gate=1, line=1))
            session.get(UiSettings, 1).active_profile_id = "p"
        self.assertEqual(import_fixture(self.sessions, self.path), (64, 36))
        self.assertEqual(import_fixture(self.sessions, self.path), (64, 36))
        with patch.dict(os.environ, {"HDGUI_DATABASE_URL": self.url}):
            command.upgrade(Config("alembic.ini"), "head")
        with self.sessions() as session:
            self.assertEqual(session.get(Gate, 1).name, "Sample gate 1")
        catalog = fixture_catalog()
        catalog["gates"][0]["name"] = "Updated sample gate"
        catalog["gates"][0]["description"] = "Updated sample description"
        catalog["channels"][0]["name"] = "Updated sample channel"
        catalog["circuits"][0]["name"] = "Updated sample circuit"
        catalog["circuits"][0]["description"] = "Updated shared sample description"
        write_fixture(self.path, catalog)
        import_fixture(self.sessions, self.path)
        with self.sessions() as session:
            descriptions = gate_descriptions(session)
            self.assertEqual(descriptions[1]["name"], "Updated sample gate")
            self.assertIn("Updated sample channel", gate_tooltip(1, descriptions))
            self.assertIn('"quotes"', descriptions[31]["description"])
            channels = session.scalars(select(Channel)).all()
            self.assertEqual(len(channels), 36)
            self.assertTrue(all(c.name and c.description for c in channels))
            circuits = session.scalars(select(Circuit)).all()
            self.assertEqual(len(circuits), 7)
            circuit = session.get(Circuit, "integration")
            self.assertEqual(circuit.name, "Updated sample circuit")
            self.assertEqual(circuit.description, "Updated shared sample description")
            profile = session.get(BirthProfile, "p")
            self.assertEqual((profile.name, profile.chart_svg, profile.activation_key),
                             ("Sample profile", "<svg/>", "activation-key"))
            self.assertEqual(profile.activation_engine_version, "sample")
            self.assertEqual(len(profile.activations), 1)
            self.assertEqual(session.get(UiSettings, 1).active_profile_id, "p")
            gate10 = descriptions[10]
            self.assertEqual(gate10["harmonic"], "20, 34, 57")

    def test_packaged_default_catalog_is_complete_and_importable(self):
        self.assertEqual(DEFAULT_PATH, Path("data/fixtures/catalog.yaml"))
        self.assertTrue(PACKAGED_DEFAULT_PATH.is_file())
        with patch("app.catalog_fixture.DEFAULT_PATH", Path(self.temp.name) / "absent.yaml"):
            self.assertEqual(import_fixture(self.sessions), (64, 36))
        with self.sessions() as session:
            gates = session.scalars(select(Gate)).all()
            channels = session.scalars(select(Channel)).all()
            circuits = session.scalars(select(Circuit)).all()
            self.assertEqual(len(gates), 64)
            self.assertTrue(all(gate.name and gate.description for gate in gates))
            self.assertEqual(len(channels), 36)
            self.assertTrue(all(channel.name and channel.description for channel in channels))
            self.assertEqual(len(circuits), 7)
            self.assertTrue(all(circuit.name and circuit.description for circuit in circuits))
            integration = {
                (channel.gate_a, channel.gate_b)
                for channel in channels if channel.circuit_id == "integration"
            }
            centering = {
                (channel.gate_a, channel.gate_b)
                for channel in channels if channel.circuit_id == "centering"
            }
            self.assertEqual(integration, {(10, 20), (10, 57), (20, 34), (34, 57)})
            self.assertEqual(centering, {(10, 34), (25, 51)})

    def test_default_resolution_prefers_local_catalog_and_explicit_path_never_falls_back(self):
        local_path = Path(self.temp.name) / "local.yaml"
        packaged_path = Path(self.temp.name) / "packaged.yaml"
        write_fixture(packaged_path)
        local_catalog = fixture_catalog()
        local_catalog["gates"][0]["name"] = "Local override"
        write_fixture(local_path, local_catalog)

        with patch("app.catalog_fixture.DEFAULT_PATH", local_path), \
                patch("app.catalog_fixture.PACKAGED_DEFAULT_PATH", packaged_path):
            import_fixture(self.sessions)
            with self.sessions() as session:
                self.assertEqual(session.get(Gate, 1).name, "Local override")
            local_path.unlink()
            import_fixture(self.sessions)
            with self.sessions() as session:
                self.assertEqual(session.get(Gate, 1).name, "Sample gate 1")
            with self.assertRaises(OSError):
                import_fixture(self.sessions, local_path)

    def test_validation_and_database_failure_roll_back(self):
        import_fixture(self.sessions, self.path)
        variants = []
        for field, value in (("number", 65), ("number", True), ("name", ""),
                             ("name", "x" * 201), ("center", "Wrong"),
                             ("harmonics", [2]), ("source_records", []), ("unexpected", "extra")):
            changed = fixture_catalog()
            changed["gates"][0][field] = value
            variants.append(changed)
        for field, value in (("sources", {}), ("source_discrepancies", [])):
            changed = fixture_catalog()
            changed[field] = value
            variants.append(changed)
        changed = fixture_catalog()
        changed["channels"][0]["source_records"] = []
        variants.append(changed)
        changed = fixture_catalog()
        changed["circuits"].pop()
        variants.append(changed)
        changed = fixture_catalog()
        changed["circuits"][-1] = changed["circuits"][0]
        variants.append(changed)
        changed = fixture_catalog()
        changed["circuits"][0]["unexpected"] = "extra"
        variants.append(changed)
        changed = fixture_catalog()
        changed["circuits"][0]["id"] = "unknown"
        variants.append(changed)
        changed = fixture_catalog()
        changed["channels"][0]["circuit_id"] = "integration"
        variants.append(changed)
        for section in ("gates", "channels"):
            changed = fixture_catalog()
            changed[section].pop()
            variants.append(changed)
            changed = fixture_catalog()
            changed[section][-1] = changed[section][0]
            variants.append(changed)
        for pair in ([1, 2], [1, 1], [0, 8], [1, 8, 10]):
            changed = fixture_catalog()
            changed["channels"][0]["gates"] = pair
            variants.append(changed)
        for version in (1, 3, True, "2"):
            changed = fixture_catalog()
            changed["schema_version"] = version
            variants.append(changed)
        for catalog in variants:
            with self.subTest(catalog=catalog["gates"][0]["number"]):
                catalog["gates"][1]["description"] = "Must not persist"
                write_fixture(self.path, catalog)
                with self.assertRaises(ValueError):
                    import_fixture(self.sessions, self.path)
                with self.sessions() as session:
                    self.assertTrue(session.get(Gate, 2).description.startswith("Sample description"))
        catalog = fixture_catalog()
        catalog["gates"][0]["description"] = "Must roll back"
        catalog["channels"][0]["name"] = "Changed"
        write_fixture(self.path, catalog)

        updates = []

        def fail_catalog_update(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith("UPDATE "):
                updates.append(statement.split()[1])
                raise RuntimeError("Synthetic write failure")

        event.listen(self.engine, "after_cursor_execute", fail_catalog_update)
        try:
            with self.assertRaises(RuntimeError):
                import_fixture(self.sessions, self.path)
        finally:
            event.remove(self.engine, "after_cursor_execute", fail_catalog_update)
        self.assertEqual(len(updates), 1, "Failure happens after a database write")
        with self.sessions() as session:
            self.assertTrue(session.get(Gate, 1).description.startswith("Sample description"))
            channel = session.scalar(select(Channel).where(Channel.gate_a == 1, Channel.gate_b == 8))
            self.assertEqual(channel.name, "Sample channel 1-8")

    def test_source_only_relations_are_not_imported_and_endpoints_are_unordered(self):
        catalog = fixture_catalog()
        catalog["channels"][0]["gates"] = [8, 1]
        write_fixture(self.path, catalog)
        import_fixture(self.sessions, self.path)
        with self.sessions() as session:
            channels = session.scalars(select(Channel)).all()
            self.assertNotIn((1, 2), {(c.gate_a, c.gate_b) for c in channels})
            pair = next(c for c in channels if (c.gate_a, c.gate_b) == (1, 8))
            self.assertEqual(pair.name, "Sample channel 1-8")

    def test_invalid_yaml_duplicate_keys_and_legacy_csv_are_rejected(self):
        import_fixture(self.sessions, self.path)
        for text in ("schema_version: 1\nschema_version: 1\n",
                     "gates: [", "!!python/object:builtins.object {}",
                     "Gate,Description,Harmonic,Channel,Notes\n", "- []\n"):
            self.path.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(ValueError):
                import_fixture(self.sessions, self.path)
        with self.sessions() as session:
            self.assertEqual(session.get(Gate, 1).name, "Sample gate 1")

    def test_explicit_null_clears_texts_but_not_relations(self):
        import_fixture(self.sessions, self.path)
        catalog = fixture_catalog()
        catalog["gates"][0]["description"] = None
        catalog["channels"][0]["name"] = None
        catalog["channels"][0]["description"] = None
        write_fixture(self.path, catalog)
        import_fixture(self.sessions, self.path)
        with self.sessions() as session:
            self.assertIsNone(session.get(Gate, 1).description)
            channel = session.scalar(select(Channel).where(Channel.gate_a == 1, Channel.gate_b == 8))
            self.assertIsNone(channel.name)
            self.assertIsNone(channel.description)
            self.assertEqual(session.get(Gate, 1).center, catalog["gates"][0]["center"])

    def test_pages_and_cached_graphs_reflect_database_updates_without_fixture(self):
        import_fixture(self.sessions, self.path)
        frame = TransitFrame(timestamp_utc=datetime(2026, 1, 1, tzinfo=UTC),
                             positions=[TransitPosition(body="Sun", longitude=1, gate=41, line=1)])
        with patch("app.api_client.ChartClient.transit", return_value=frame) as calculate:
            with TestClient(create_app(Settings(database_url=self.url))) as web:
                catalog = web.get("/gates")
                channels = web.get("/channels")
                self.assertEqual(len(re.findall(r"<article ", catalog.text)), 64)
                self.assertEqual(len(re.findall(r"<article ", channels.text)), 36)
                self.assertIn("Sample channel 10-57", channels.text)
                integration = channels.text.split('id="circuit-integration"')[1].split("</section>", 1)[0]
                centering = channels.text.split('id="circuit-centering"')[1].split("</section>", 1)[0]
                self.assertIn("Sample circuit integration", integration)
                self.assertIn('href="#channel-10-57"', integration)
                self.assertNotIn('href="#channel-10-34"', integration)
                self.assertIn("Sample circuit centering", centering)
                self.assertIn('href="#channel-10-34"', centering)
                self.assertNotIn('href="#channel-10-57"', centering)
                channel_10_57 = channels.text.split('id="channel-10-57"')[1].split("</article>", 1)[0]
                channel_10_34 = channels.text.split('id="channel-10-34"')[1].split("</article>", 1)[0]
                self.assertIn('href="#circuit-integration"', channel_10_57)
                self.assertIn('href="#circuit-centering"', channel_10_34)
                gate10 = catalog.text.split('id="gate-10"')[1].split("</article>")[0]
                for partner in (20, 34, 57):
                    self.assertIn(f'href="/channels#channel-10-{partner}"', gate10)
                    self.assertIn(f'href="/gates#gate-{partner}"', gate10)
                for route in ("/", "/transit", "/experiments/gate-walk"):
                    response = web.get(route)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("Sample description 41", response.text)
                catalog = fixture_catalog()
                catalog["gates"][40]["description"] = "New sample text after import"
                write_fixture(self.path, catalog)
                import_fixture(self.sessions, self.path)
                self.path.unlink()
                for route in ("/gates", "/", "/transit", "/experiments/gate-walk"):
                    response = web.get(route)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("New sample text after import", response.text)
                self.assertEqual(calculate.call_count, 1)

    def test_catalog_connections_are_app_local_and_schema_errors_visible(self):
        with TestClient(create_app(Settings(database_url=self.url))) as first:
            import_fixture(self.sessions, self.path)
            missing_url = f"sqlite:///{Path(self.temp.name) / 'no-schema.db'}"
            with TestClient(create_app(Settings(database_url=missing_url))) as second:
                for route in ("/gates", "/channels", "/experiments/gate-walk"):
                    response = second.get(route)
                    self.assertEqual(response.status_code, 500)
                    self.assertIn("Alembic", response.text)
                self.assertIn("Sample description 41", first.get("/experiments/gate-walk").text)
