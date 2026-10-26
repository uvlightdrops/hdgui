import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.api_client import ChartServiceError
from app.bodygraph import CHANNELS, GATE_CENTERS
from app.center_timeline import combined_definition
from app.config import Settings
from app.database import make_database
from app.experiments import SampleBusy
from app.main import create_app
from app.models import BirthProfile, Gate, UiSettings
from app.natal import NatalActivations, Topology
from app.transit import TransitFrame, TransitPosition

NOW = datetime(2026, 10, 25, tzinfo=UTC)
TOPOLOGY = Topology(GATE_CENTERS, tuple((a, b, None) for a, b in CHANNELS))


def positions(*gates):
    return [TransitPosition(body=f"Body{index}", longitude=index * 10, gate=gate, line=1)
            for index, gate in enumerate(gates)]


def birth(personality, design):
    return {"personality": [item.model_dump() for item in positions(*personality)],
            "design": [item.model_dump() for item in positions(*design)]}


class CenterDefinitionTests(unittest.TestCase):
    def centers(self, natal, *gates):
        result = combined_definition(natal, TransitFrame(timestamp_utc=NOW, positions=positions(*gates)), TOPOLOGY)
        return {center["id"] for center in result["centers"]}

    def test_hanging_gate_does_not_define_center(self):
        self.assertEqual(self.centers(birth([64], [61]), 63), set())

    def test_natal_transit_and_mixed_channels(self):
        natal = birth([64, 20], [34, 61])
        self.assertEqual(self.centers(natal, 1), {"Throat", "Sacral"})
        self.assertEqual(self.centers(natal, 47), {"Throat", "Sacral", "Head", "Ajna"})
        self.assertEqual(self.centers(natal, 3, 60), {"Throat", "Sacral", "Root"})

    def test_each_canonical_channel_defines_both_centers(self):
        for a, b in CHANNELS:
            with self.subTest(channel=(a, b)):
                self.assertEqual(self.centers(birth([a], [a]), b), {GATE_CENTERS[a], GATE_CENTERS[b]})


class CenterTimelineTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.url = f"sqlite:///{Path(directory.name) / 'centers.db'}"
        with patch.dict(os.environ, {"HDGUI_DATABASE_URL": self.url}):
            command.upgrade(Config("alembic.ini"), "head")
        self.engine, self.sessions = make_database(self.url)
        self.addCleanup(self.engine.dispose)
        with self.sessions.begin() as session:
            session.add(BirthProfile(
                id="p1", name="Testprofil", birth_local=datetime(2000, 1, 1),
                birth_utc=datetime(2000, 1, 1), timezone="Etc/UTC", place="Test", chart_key="test",
            ))
            session.flush()
            session.get(UiSettings, 1).active_profile_id = "p1"
        self.client = Mock()
        self.client.natal.return_value = NatalActivations(
            personality=positions(64, 20), design=positions(34, 61),
        )
        self.client.transit.side_effect = lambda timestamp: TransitFrame(
            timestamp_utc=timestamp, positions=positions(47),
        )
        self.web = TestClient(create_app(Settings(database_url=self.url), self.client))
        self.web.__enter__()
        self.addCleanup(self.web.__exit__, None, None, None)
        self.params = {"profile_id": "p1", "timestamp": NOW.isoformat()}

    def test_page_and_no_profile_state_do_not_calculate(self):
        response = self.web.get("/centers")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-profile-id="p1"', response.text)
        self.assertIn('class="center-layout"', response.text)
        self.assertIn('class="center-bodygraph-panel"', response.text)
        self.assertIn('class="center-timeline-panel"', response.text)
        self.assertLess(response.text.index('id="center-graph"'),
                        response.text.index('class="center-timeline-panel"'))
        self.assertLess(response.text.index('id="center-tracks"'),
                        response.text.index('class="center-controls"'))
        self.assertLess(response.text.index('class="center-controls"'),
                        response.text.index('id="center-range"'))
        self.assertNotIn("Testprofil + Transit", response.text)
        self.assertNotIn("<h1>", response.text)
        self.assertNotIn("Kanalhälften:", response.text)
        self.assertNotIn('id="center-table"', response.text)
        self.assertNotIn("Abtastpunkte als Tabelle", response.text)
        self.assertIn('href="/knowledge#center-timeline"', response.text)
        self.assertEqual(response.text.count('type="checkbox" data-center='), 9)
        with self.sessions.begin() as session:
            session.get(UiSettings, 1).active_profile_id = None
        response = self.web.get("/centers")
        self.assertIn("Kein aktuelles Geburtsprofil ausgewählt", response.text)
        self.client.natal.assert_not_called()
        self.client.transit.assert_not_called()

    def test_knowledge_groups_catalog_and_explains_center_timeline_without_api(self):
        response = self.web.get("/knowledge")
        self.assertEqual(response.status_code, 200)
        self.assertIn("KI-Autorenhinweis", response.text)
        self.assertIn("2026-10-25", response.text)
        self.assertIn("keine exakten Wechselzeitpunkte", response.text)
        self.assertIn('id="center-timeline"', response.text)
        self.assertIn('class="sidebar-group sidebar-knowledge" open', response.text)
        self.assertIn('href="/gates"', response.text)
        self.assertIn('href="/channels"', response.text)
        for path in ("/gates", "/channels"):
            page = self.web.get(path)
            self.assertEqual(page.status_code, 200)
            self.assertIn('class="sidebar-group sidebar-knowledge" open', page.text)
            self.assertIn(f'href="{path}" aria-current="page"', page.text)
        self.client.natal.assert_not_called()
        self.client.transit.assert_not_called()

    def test_profile_navigation_groups_creation_and_marks_only_current_link(self):
        for path in ("/profiles", "/profiles/new"):
            response = self.web.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('class="sidebar-group" open', response.text)
            sidebar = response.text.split('<nav class="sidebar"', 1)[1].split("</nav>", 1)[0]
            self.assertIn(f'href="{path}" aria-current="page"', sidebar)
            self.assertEqual(sidebar.count('aria-current="page"'), 1)
            self.assertLess(sidebar.index("<summary>Geburtsprofile</summary>"),
                            sidebar.index('href="/profiles/new"'))

    def test_celestial_knowledge_table_is_accessible_and_needs_no_api(self):
        response = self.web.get("/knowledge/celestial-bodies")
        self.assertEqual(response.status_code, 200)
        self.assertIn("KI-Autorenhinweis", response.text)
        self.assertIn("2026-10-25", response.text)
        self.assertIn("nicht identifiziert", response.text)
        self.assertIn("keine wissenschaftlich belegten Wirkungen", response.text)
        self.assertEqual(response.text.count('scope="row"'), 13)
        for symbol in ("☉", "⊕", "☽", "☿", "♀", "♂", "♃", "♄", "♅", "♆", "♇", "☊", "☋"):
            self.assertIn(symbol, response.text)
        self.assertIn("kein verbindliches Lebensziel", response.text)
        self.assertIn('href="/knowledge/celestial-bodies" aria-current="page"', response.text)
        self.assertIn('class="sidebar-group sidebar-knowledge" open', response.text)
        self.client.natal.assert_not_called()
        self.client.transit.assert_not_called()

    def test_frame_and_graph_use_union_and_canonical_definition(self):
        response = self.web.get("/centers/frame", params={**self.params, "graph": True})
        self.assertEqual(response.status_code, 200, response.text)
        frame = response.json()
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual({item["id"] for item in frame["centers"]}, {"Head", "Ajna", "Throat", "Sacral"})
        self.assertEqual({item["id"] for item in frame["natal_centers"]}, {"Throat", "Sacral"})
        root = ET.fromstring(frame["svg"])
        svg_centers = {item.get("data-center") for item in root.iter() if item.get("data-defined") == "true"}
        self.assertEqual(svg_centers, {item["id"] for item in frame["centers"]})
        sources = {item.get("data-source") for item in root.iter() if item.get("data-source")}
        self.assertEqual(sources, {"personality", "design", "transit"})
        gates = {int(item.get("data-active-gate")) for item in root.iter() if item.get("data-active-gate")}
        self.assertEqual(gates, {20, 34, 47, 61, 64})
        self.assertNotIn("svg", self.web.get("/centers/frame", params=self.params).json())
        self.web.get("/experiments/frame", params={"timestamp": NOW.isoformat()})
        self.assertEqual(self.client.transit.call_count, 1)
        self.assertEqual(self.client.natal.call_count, 1)

    def test_validation_and_wrong_profile_do_not_calculate(self):
        for change, code in (
            ({"timestamp": "2026-10-25T00:00"}, 422),
            ({"timestamp": "2100-01-01T00:00Z"}, 422),
            ({"profile_id": "missing"}, 409),
        ):
            response = self.web.get("/centers/frame", params={**self.params, **change})
            self.assertEqual(response.status_code, code, response.text)
        self.client.natal.assert_not_called()
        self.client.transit.assert_not_called()

    def test_graph_cache_reuses_svg_and_invalidates_catalog_and_birth_changes(self):
        from app.bodygraph import render_bodygraph
        params = {**self.params, "graph": True}
        with patch("app.center_timeline.render_bodygraph", wraps=render_bodygraph) as render:
            first = self.web.get("/centers/frame", params=params)
            second = self.web.get("/centers/frame", params=params)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["svg"], second.json()["svg"])
            self.assertEqual(render.call_count, 1)
            with self.sessions.begin() as session:
                session.get(Gate, 64).name = "Updated catalog label"
            changed = self.web.get("/centers/frame", params=params)
            self.assertIn("Updated catalog label", changed.json()["svg"])
            self.assertEqual(render.call_count, 2)
            with self.sessions.begin() as session:
                session.get(BirthProfile, "p1").birth_utc = datetime(2002, 1, 1)
            changed_birth = self.web.get("/centers/frame", params=params)
            self.assertEqual(changed_birth.status_code, 200)
            self.assertEqual(render.call_count, 3)
        self.assertEqual(self.client.transit.call_count, 1)
        self.assertEqual(self.client.natal.call_count, 2)

    def test_errors_do_not_return_success_shaped_data(self):
        self.client.transit.side_effect = ChartServiceError("Dienst offline")
        response = self.web.get("/centers/frame", params=self.params)
        self.assertEqual(response.status_code, 502)
        self.assertIn("Dienst offline", response.text)
        self.assertNotIn("centers", response.json())
        with patch("app.experiments.SampleCache.sample", side_effect=SampleBusy()):
            response = self.web.get("/centers/frame", params=self.params)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["retry-after"], "1")

    def test_profile_edited_during_sample_is_rejected(self):
        def transit(timestamp):
            with self.sessions.begin() as session:
                session.get(BirthProfile, "p1").birth_utc = datetime(2001, 1, 1)
            return TransitFrame(timestamp_utc=timestamp, positions=positions(47))
        self.client.transit.side_effect = transit
        response = self.web.get("/centers/frame", params=self.params)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("svg", response.json())

    def test_profile_deselected_during_sample_is_rejected(self):
        def transit(timestamp):
            with self.sessions.begin() as session:
                session.get(UiSettings, 1).active_profile_id = None
            return TransitFrame(timestamp_utc=timestamp, positions=positions(47))
        self.client.transit.side_effect = transit
        self.assertEqual(self.web.get("/centers/frame", params=self.params).status_code, 409)


if __name__ == "__main__":
    unittest.main()
