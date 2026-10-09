import json
import os
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import httpx
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text

from app.api_client import ChartClient, ChartServiceError
from app.config import Settings
from app.database import make_database
from app.main import create_app
from app.bodygraph import CHANNELS, GATE_CENTERS
from app.models import BirthActivation, BirthProfile, Channel, Circuit, Gate, UiSettings
from app.natal import NatalActivations, Topology, derive, load_topology
from catalog_fixture_helpers import CIRCUIT_CHANNELS

BODIES = ("Sun", "Earth", "Moon")
PRS = [{"Planet": "Sun", "Lon": 10.5, "Gate": 64, "Line": 2},
       {"Planet": "Earth", "Lon": 190.5, "Gate": 47, "Line": 2},
       {"Planet": "Moon", "Lon": 33.0, "Gate": 20, "Line": 1}]
DES = [{"Planet": "Sun", "Lon": 100.0, "Gate": 34, "Line": 5},
       {"Planet": "Earth", "Lon": 280.0, "Gate": 20, "Line": 5},
       {"Planet": "Moon", "Lon": 359.9999, "Gate": 64, "Line": 6}]
SVG = '<svg xmlns="http://www.w3.org/2000/svg"><text>Chart</text></svg>'


def natal(prs=PRS, des=DES):
    from app.transit import TransitPosition
    return NatalActivations(personality=[TransitPosition.model_validate(p) for p in prs],
                            design=[TransitPosition.model_validate(p) for p in des])


class FakeClient:
    def __init__(self):
        self.natal_calls = []
        self.render_calls = 0
        self.fail = False
        self.during = None

    def natal(self, birth_utc):
        self.natal_calls.append(birth_utc)
        if self.during:
            self.during()
        if self.fail:
            raise ChartServiceError("Der Berechnungsdienst ist nicht erreichbar.")
        return natal()

    def render(self, profile):
        self.render_calls += 1
        return SVG


def upgrade(url, revision="head"):
    with patch.dict(os.environ, {"HDGUI_DATABASE_URL": url}):
        command.upgrade(Config("alembic.ini"), revision)


def downgrade(url, revision):
    with patch.dict(os.environ, {"HDGUI_DATABASE_URL": url}):
        command.downgrade(Config("alembic.ini"), revision)


class MigrationTests(unittest.TestCase):
    def test_upgrade_preserves_settings_and_profiles_and_downgrades(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "m.db"
            url = f"sqlite:///{path}"
            upgrade(url, "0002")
            with sqlite3.connect(path) as db:
                db.execute("UPDATE ui_settings SET cache_seconds=600, autoreload=0")
                db.execute("INSERT INTO birth_profiles VALUES ('p1','Alt','1990-01-01 00:00:00',"
                           "'Etc/UTC','1990-01-01 00:00:00','X','k',NULL,NULL)")
            upgrade(url)
            engine, sessions = make_database(url)
            names = inspect(engine).get_table_names()
            self.assertIn("birth_activations", names)
            fks = inspect(engine).get_foreign_keys("ui_settings")
            self.assertEqual(fks[0]["referred_table"], "birth_profiles")
            self.assertEqual(fks[0]["options"].get("ondelete"), "SET NULL")
            with sessions.begin() as session:
                settings = session.get(UiSettings, 1)
                self.assertEqual((settings.cache_seconds, settings.autoreload), (600, False))
                self.assertIsNone(settings.active_profile_id)
                settings.active_profile_id = "p1"
                self.assertEqual(session.get(BirthProfile, "p1").name, "Alt")
                self.assertEqual(session.get(BirthProfile, "p1").activations, [])
                self.assertIsNone(session.get(BirthProfile, "p1").activation_key)
                session.add(BirthActivation(profile_id="p1", source="design", body="Sun",
                                            longitude=1, gate=1, line=1))
            with engine.begin() as connection:
                # Database-level safety net, even outside the ORM.
                connection.execute(text("DELETE FROM birth_profiles WHERE id='p1'"))
                self.assertIsNone(connection.scalar(text("SELECT active_profile_id FROM ui_settings")))
                self.assertEqual(connection.scalar(text("SELECT count(*) FROM birth_activations")), 0)
                with self.assertRaises(Exception):
                    connection.execute(text(
                        "INSERT INTO birth_activations (profile_id, source, body, longitude, gate, line) "
                        "VALUES ('missing', 'design', 'Sun', 1, 1, 1)"))
            engine.dispose()
            downgrade(url, "0002")
            upgrade(url)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT cache_seconds FROM ui_settings").fetchone()[0], 600)
                self.assertEqual(db.execute("SELECT count(*) FROM gates").fetchone()[0], 64)
                self.assertEqual(db.execute("SELECT name FROM birth_profiles").fetchall(), [])

    def test_circuit_migration_preserves_existing_catalog_text(self):
        with tempfile.TemporaryDirectory() as temp:
            url = f"sqlite:///{Path(temp) / 'preserve.db'}"
            upgrade(url, "0003")
            with sqlite3.connect(Path(temp) / "preserve.db") as db:
                db.execute("UPDATE gates SET name='Existing gate', description='Existing gate text' WHERE number=1")
                db.execute(
                    "UPDATE channels SET name='Existing channel', description='Existing channel text' "
                    "WHERE gate_a=1 AND gate_b=8"
                )
            upgrade(url)
            engine, sessions = make_database(url)
            with sessions() as session:
                self.assertEqual(
                    (session.get(Gate, 1).name, session.get(Gate, 1).description),
                    ("Existing gate", "Existing gate text"),
                )
                channel = session.scalar(select(Channel).where(Channel.gate_a == 1, Channel.gate_b == 8))
                self.assertEqual((channel.name, channel.description), ("Existing channel", "Existing channel text"))
                self.assertEqual(channel.circuit_id, "knowing")
            engine.dispose()

    def test_activation_constraints(self):
        with tempfile.TemporaryDirectory() as temp:
            url = f"sqlite:///{Path(temp) / 'c.db'}"
            upgrade(url)
            engine, sessions = make_database(url)
            with sessions.begin() as session:
                session.add(BirthProfile(id="p", name="n", birth_local=datetime(2000, 1, 1),
                                         timezone="Etc/UTC", birth_utc=datetime(2000, 1, 1),
                                         place="x", chart_key="k"))
            for values in ({"source": "transit"}, {"gate": 65}, {"line": 0}, {"longitude": 360}):
                with self.assertRaises(Exception), sessions.begin() as session:
                    session.add(BirthActivation(**{"profile_id": "p", "source": "design", "body": "Sun",
                                                   "longitude": 1, "gate": 1, "line": 1, **values}))
            with self.assertRaises(Exception), sessions.begin() as session:
                for _ in range(2):
                    session.add(BirthActivation(profile_id="p", source="design", body="Sun",
                                                longitude=1, gate=1, line=1))
            engine.dispose()


class CanonicalTopologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.url = f"sqlite:///{Path(cls.temp.name) / 'canon.db'}"
        upgrade(cls.url)
        cls.engine, cls.sessions = make_database(cls.url)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        cls.temp.cleanup()

    def test_counts_and_agreement_with_constants(self):
        with self.sessions() as session:
            gates = {gate.number: gate for gate in session.scalars(select(Gate))}
            channels = session.scalars(select(Channel).order_by(Channel.id)).all()
            circuits = session.scalars(select(Circuit)).all()
            topology = load_topology(session)
        self.assertEqual(set(gates), set(range(1, 65)))
        self.assertEqual(len(channels), 36)
        self.assertEqual({circuit.id for circuit in circuits}, set(CIRCUIT_CHANNELS))
        expected = {
            tuple(sorted(pair)): circuit_id
            for circuit_id, members in CIRCUIT_CHANNELS.items()
            for pair in members
        }
        self.assertEqual({(c.gate_a, c.gate_b): c.circuit_id for c in channels}, expected)
        self.assertEqual(
            {circuit_id: sum(channel.circuit_id == circuit_id for channel in channels)
             for circuit_id in CIRCUIT_CHANNELS},
            {"integration": 4, "centering": 2, "knowing": 9, "ego": 5,
             "defense": 2, "logic": 7, "sensing": 7},
        )
        self.assertEqual({(c.gate_a, c.gate_b) for c in channels}, {(min(p), max(p)) for p in CHANNELS})
        self.assertTrue(all(c.gate_a < c.gate_b for c in channels))
        self.assertEqual(topology.gate_centers, GATE_CENTERS)
        for number, gate in gates.items():
            self.assertEqual(gate.name, f"Gate {number}")
            self.assertIsNone(gate.description)
        self.assertEqual(gates[20].center, "Throat")
        self.assertEqual(gates[57].center, "Spleen")

    def test_multichannel_gates(self):
        with self.sessions() as session:
            channels = {(c.gate_a, c.gate_b): c for c in session.scalars(select(Channel))}

        def partners(gate):
            return sorted(b if a == gate else a for a, b in channels if gate in (a, b))

        self.assertEqual(partners(10), [20, 34, 57])
        self.assertEqual(partners(20), [10, 34, 57])
        self.assertEqual(partners(34), [10, 20, 57])
        self.assertEqual(partners(57), [10, 20, 34])
        self.assertEqual(partners(16), [48])
        self.assertEqual(sum(len(partners(gate)) for gate in range(1, 65)), 72)
        for channel in channels.values():
            self.assertIsNone(channel.name)
            self.assertIsNone(channel.description)

    def test_referential_constraints(self):
        statements = (
            "INSERT INTO channels (id, gate_a, gate_b) VALUES (99, 1, 65)",
            "INSERT INTO channels (id, gate_a, gate_b) VALUES (99, 8, 1)",
            "INSERT INTO channels (id, gate_a, gate_b) VALUES (99, 1, 8)",
            "INSERT INTO gates (number, name, center) VALUES (65, 'x', 'G')",
            "INSERT INTO gates (number, name, center) VALUES (0, 'x', 'G')",
            "UPDATE gates SET center = 'Nowhere' WHERE number = 1",
            "DELETE FROM gates WHERE number = 1",
        )
        for statement in statements:
            with self.subTest(statement=statement), self.assertRaises(Exception):
                with self.engine.begin() as connection:
                    connection.execute(text(statement))
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Channel)), 36)
        fks = inspect(self.engine).get_foreign_keys("channels")
        self.assertIn(("circuits", ["id"]), [(fk["referred_table"], fk["referred_columns"]) for fk in fks])

    def test_activation_gate_references_canonical_gates(self):
        fks = inspect(self.engine).get_foreign_keys("birth_activations")
        self.assertIn(("gates", ["number"]), [(fk["referred_table"], fk["referred_columns"]) for fk in fks])

    def test_seed_topology_is_frozen_and_has_no_fixture_dependency(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "migration_0003", Path("migrations/versions/0003_birth_activations.py"))
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual(migration.CHANNELS, CHANNELS, "Frozen seed equals app.bodygraph")
        frozen = {g: c for c, gates in migration.GATE_CENTERS.items() for g in gates}
        self.assertEqual(frozen, GATE_CENTERS)
        self.assertFalse(hasattr(migration, "CSV_PATH"))
        self.assertFalse(hasattr(migration, "read_csv"))


class NatalParsingTests(unittest.TestCase):
    def setUp(self):
        self.client = ChartClient(Settings(api_token="test-only"))
        self.birth = datetime(1990, 7, 15, 12, 30, 5)

    def response(self, payload, status=200):
        return httpx.Response(status, content=json.dumps(payload),
                              request=httpx.Request("GET", "http://127.0.0.1:9021/calculate"))

    def test_both_sources_and_utc_parameters(self):
        payload = {"gates": {"prs": {"Planets": PRS}, "des": {"Planets": DES}}}
        with patch("app.api_client.httpx.get", return_value=self.response(payload)) as get:
            result = self.client.natal(self.birth)
        params = get.call_args.kwargs["params"]
        self.assertEqual((params["hour"], params["minute"], params["second"]), (12, 30, 5))
        self.assertEqual((params["place"], params["latitude"], params["longitude"]), ("Etc/UTC", 0, 0))
        self.assertTrue(get.call_args.args[0].endswith("/calculate"))
        self.assertEqual([p.gate for p in result.personality], [64, 47, 20])
        self.assertEqual([p.gate for p in result.design], [34, 20, 64])
        self.assertEqual(result.design[2].longitude, 359.9999)

    def test_invalid_payloads_rejected(self):
        bad = [
            {}, [], {"gates": {"prs": {"Planets": PRS}}},
            {"gates": {"prs": {"Planets": PRS}, "des": {"Planets": []}}},
            {"gates": {"prs": {"Planets": []}, "des": {"Planets": DES}}},
            {"gates": {"prs": {"Planets": PRS + [PRS[0]]}, "des": {"Planets": DES}}},
            {"gates": {"prs": {"Planets": PRS[:2]}, "des": {"Planets": DES}}},
            {"gates": {"prs": {"Planets": PRS}, "des": {"Planets": [{**DES[0], "Gate": 65}] + DES[1:]}}},
            {"gates": {"prs": {"Planets": PRS}, "des": {"Planets": [{"invalid": True}]}}},
            {"gates": {"prs": {"Planets": PRS}, "des": None}},
        ]
        for payload in bad:
            with self.subTest(payload=payload), \
                    patch("app.api_client.httpx.get", return_value=self.response(payload)):
                with self.assertRaises(ChartServiceError):
                    self.client.natal(self.birth)
        with patch("app.api_client.httpx.get", return_value=self.response({}, 500)):
            with self.assertRaisesRegex(ChartServiceError, "HTTP 500"):
                self.client.natal(self.birth)
        with patch("app.api_client.httpx.get", side_effect=httpx.ConnectError("offline")):
            with self.assertRaises(ChartServiceError):
                self.client.natal(self.birth)
        with self.assertRaises(ChartServiceError):
            ChartClient(Settings(api_token="")).natal(self.birth)

    def test_derive_uses_canonical_topology(self):
        data = natal()
        lists = {source: [p.model_dump() for p in getattr(data, source)]
                 for source in ("personality", "design")}
        topology = Topology(GATE_CENTERS, tuple((min(p), max(p), None) for p in CHANNELS))
        result = derive(lists, topology)
        self.assertEqual([c["gates"] for c in result["channels"]], [[47, 64], [20, 34]])
        self.assertEqual(derive(lists, Topology(GATE_CENTERS, ()))["channels"], [],
                         "Channels come only from the supplied canonical topology")
        self.assertEqual({c["id"] for c in result["centers"]}, {"Head", "Ajna", "Throat", "Sacral"})
        gates = {g["gate"]: g["sources"] for g in result["gates"]}
        self.assertEqual(gates[64], ["personality", "design"])
        self.assertEqual(gates[34], ["design"])


class CurrentProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.url = f"sqlite:///{Path(self.temp.name) / 'test.db'}"
        self.settings = Settings(database_url=self.url, session_secret="test-only")
        upgrade(self.url)
        self.fake = FakeClient()
        self.client = TestClient(create_app(self.settings, self.fake))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        html = self.client.get("/profiles/new").text
        self.csrf = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
        self.engine, self.sessions = make_database(self.url)
        self.addCleanup(self.engine.dispose)

    def create(self, name="Anna", birth="1990-07-15T14:30:00"):
        data = {"csrf": self.csrf, "name": name, "birth_local": birth,
                "timezone": "Europe/Berlin", "place": "Berlin"}
        self.assertEqual(self.client.post("/profiles", data=data, follow_redirects=False).status_code, 303)
        with self.sessions() as session:
            return session.scalar(select(BirthProfile.id).where(BirthProfile.name == name))

    def current(self):
        with self.sessions() as session:
            return session.get(UiSettings, 1).active_profile_id

    def activation_count(self):
        with self.sessions() as session:
            return session.scalar(select(func.count()).select_from(BirthActivation))

    def select(self, profile_id, csrf=None):
        return self.client.post(f"/profiles/{profile_id}/current", data={"csrf": csrf or self.csrf},
                                follow_redirects=False)

    def test_select_switch_clear_and_markup(self):
        first, second = self.create("Anna"), self.create("Bert")
        page = self.client.get("/profiles").text
        self.assertEqual(page.count("Als aktuell setzen</button>"), 2)
        self.assertIn("Kein aktuelles Profil gewählt", page)
        self.assertEqual(self.select(first, "wrong").status_code, 403)
        self.assertIsNone(self.current())
        self.assertEqual(self.select("missing").status_code, 404)
        self.assertEqual(self.select(first).status_code, 303)
        self.assertEqual(self.current(), first)
        page = self.client.get("/profiles").text
        self.assertIn('class="current-badge">Aktuell</span>', page)
        self.assertIn('aria-current="true"', page)
        self.assertEqual(page.count("Als aktuell setzen</button>"), 1)
        self.select(second)
        self.assertEqual(self.current(), second)
        self.assertEqual(self.fake.natal_calls, [], "Selecting must not call the API")
        self.assertEqual(self.client.post("/profiles/current/clear", data={"csrf": ""}).status_code, 403)
        self.client.post("/profiles/current/clear", data={"csrf": self.csrf})
        self.assertIsNone(self.current())

    def test_delete_active_clears_reference_and_cascades(self):
        first, second = self.create("Anna"), self.create("Bert")
        self.select(first)
        self.assertEqual(self.client.get("/profiles/current/overlay").status_code, 200)
        self.assertEqual(self.activation_count(), 6)
        self.client.post(f"/profiles/{second}/delete", data={"csrf": self.csrf})
        self.assertEqual(self.current(), first, "Deleting another profile keeps the selection")
        self.client.post(f"/profiles/{first}/delete", data={"csrf": self.csrf})
        self.assertIsNone(self.current())
        self.assertEqual(self.activation_count(), 0)
        self.assertIn("Kein aktuelles Geburtsprofil", self.client.get("/profiles/current/overlay").json()["detail"])
        self.assertIn("disabled", self.client.get("/experiments/gate-plot").text)

    def test_overlay_cache_reuse_and_invalidation(self):
        profile_id = self.create()
        self.assertIsNone(self.client.get("/profiles/current/overlay").json()["profile"])
        self.select(profile_id)
        page = self.client.get("/experiments/gate-plot").text
        self.assertIn('id="timeline-natal" data-url="/profiles/current/overlay"', page)
        self.assertIn("Aktuelles Profil einblenden: <strong>Anna</strong>", page)
        self.assertEqual(self.fake.natal_calls, [], "Pages never calculate activations")
        response = self.client.get("/profiles/current/overlay")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        data = response.json()
        self.assertEqual(data["profile"]["name"], "Anna")
        self.assertEqual(data["profile"]["birth_utc"], "1990-07-15T12:30:00+00:00")
        self.assertEqual([p["gate"] for p in data["personality"]], [64, 47, 20])
        self.assertEqual([p["gate"] for p in data["design"]], [34, 20, 64])
        self.assertEqual([c["gates"] for c in data["channels"]], [[47, 64], [20, 34]])
        self.assertEqual([c["name"] for c in data["channels"]], [None, None],
                         "Fresh migrations seed topology without private channel texts")
        self.assertIn("Sacral", [c["id"] for c in data["centers"]])
        self.assertEqual(self.fake.natal_calls, [datetime(1990, 7, 15, 12, 30)])
        self.assertEqual(self.client.get("/profiles/current/overlay").json(), data)
        self.assertEqual(len(self.fake.natal_calls), 1, "Stored activations are reused")
        profiles_page = self.client.get("/profiles").text
        self.assertIn("Aktivierungen (4 Tore, 2 Kanäle)", profiles_page)
        self.assertIn("Sonne 64.2, Erde 47.2, Mond 20.1", profiles_page)
        base = {"csrf": self.csrf, "birth_local": "1990-07-15T14:30:00",
                "timezone": "Europe/Berlin", "place": "Hamburg"}
        self.client.post(f"/profiles/{profile_id}", data={**base, "name": "Anna B."})
        self.assertEqual(self.activation_count(), 6, "Renaming keeps activations")
        self.assertEqual(self.client.get("/profiles/current/overlay").json()["profile"]["name"], "Anna B.")
        self.assertEqual(len(self.fake.natal_calls), 1)
        self.client.post(f"/profiles/{profile_id}", data={**base, "name": "Anna B.",
                                                           "birth_local": "1990-07-15T15:30:00"})
        self.assertEqual(self.activation_count(), 0, "Time edit invalidates activations")
        self.assertIn("Aktivierungen noch nicht berechnet", self.client.get("/profiles").text)
        self.client.get("/profiles/current/overlay")
        self.assertEqual(self.fake.natal_calls[-1], datetime(1990, 7, 15, 13, 30))
        settings = Settings(database_url=self.url, engine_version="next", session_secret="test-only")
        with TestClient(create_app(settings, self.fake)) as updated:
            self.assertIn("Aktivierungen noch nicht berechnet", updated.get("/profiles").text)
            self.assertEqual(updated.get("/profiles/current/overlay").json()["engine_version"], "next")
        self.assertEqual(len(self.fake.natal_calls), 3, "Engine version change recalculates")
        self.assertEqual(self.activation_count(), 6, "Recalculation replaces rows")

    def test_api_failure_and_independence_from_svg(self):
        profile_id = self.create()
        self.select(profile_id)
        self.client.post(f"/profiles/{profile_id}/chart", data={"csrf": self.csrf})
        self.fake.fail = True
        response = self.client.get("/profiles/current/overlay")
        self.assertEqual(response.status_code, 502)
        self.assertIn("nicht erreichbar", response.json()["detail"])
        self.assertEqual(self.activation_count(), 0)
        with self.sessions() as session:
            self.assertEqual(session.get(BirthProfile, profile_id).chart_svg, SVG)
        self.fake.fail = False
        self.assertEqual(self.client.get("/profiles/current/overlay").status_code, 200)
        self.assertEqual(self.client.get(f"/profiles/{profile_id}/chart.svg").text, SVG)
        self.assertEqual(self.fake.render_calls, 1)

    def test_edit_during_calculation_is_not_persisted(self):
        profile_id = self.create()
        self.select(profile_id)

        def edit():
            with self.sessions.begin() as session:
                session.get(BirthProfile, profile_id).birth_utc = datetime(2001, 1, 1)
            self.fake.during = None

        self.fake.during = edit
        response = self.client.get("/profiles/current/overlay")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.activation_count(), 0)
        self.assertEqual(self.client.get("/profiles/current/overlay").json()["profile"]["birth_utc"],
                         "2001-01-01T00:00:00+00:00")

    def test_gates_notes_are_not_applied(self):
        page = self.client.get("/gates").text
        self.assertNotIn("historisches Geburtsprofil", page)
        self.assertNotIn("Notizen", page)
        self.assertEqual(self.fake.natal_calls, [])

    def test_gate_catalog_current_profile_and_lazy_activation_endpoint(self):
        profile_id = self.create()
        self.select(profile_id)
        page = self.client.get("/gates")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertIn('id="catalog-activations" type="checkbox" checked', page.text)
        self.assertIn(f'data-profile-id="{profile_id}"', page.text)
        self.assertIn("<strong>Anna</strong>", page.text)
        self.assertIn('/static/gate-catalog.js', page.text)
        self.assertEqual(self.fake.natal_calls, [], "Catalog HTML must not block on the API")
        data = self.client.get("/profiles/current/overlay").json()
        gates = {item["gate"]: item["sources"] for item in data["gates"]}
        self.assertEqual(gates[64], ["personality", "design"])
        self.assertEqual(gates[47], ["personality"])
        self.assertEqual(gates[34], ["design"])
        self.client.get("/profiles/current/overlay")
        self.assertEqual(len(self.fake.natal_calls), 1)


if __name__ == "__main__":
    unittest.main()
