import os
import re
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, patch

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import make_database
from app.hd_catalog import CATEGORY_LABELS, import_entities
from app.knowledge import TOPICS, knowledge_url, validate_mapping
from app.main import create_app
from app.models import BirthProfile, KnowledgeEntityText, UiSettings


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.url = f"sqlite:///{Path(directory.name) / 'knowledge.db'}"
        with patch.dict(os.environ, {"HDGUI_DATABASE_URL": self.url}):
            command.upgrade(Config("alembic.ini"), "head")
        self.engine, self.sessions = make_database(self.url)
        self.addCleanup(self.engine.dispose)
        import_entities(self.sessions)
        self.api = Mock()
        self.web = TestClient(create_app(Settings(database_url=self.url), self.api))
        self.web.__enter__()
        self.addCleanup(self.web.__exit__, None, None, None)

    def test_topics_cover_categories_and_need_no_calculation(self):
        validate_mapping()
        for area, (_, categories) in TOPICS.items():
            for category in categories:
                with self.subTest(category=category):
                    page = self.web.get(knowledge_url(category))
                    self.assertEqual(page.status_code, 200, page.text)
                    topics = page.text.split('aria-label="Wissensbereiche">')[1].split("</nav>")[0]
                    self.assertEqual(topics.count("<a "), 4)
                    self.assertEqual(topics.count('aria-current="page"'), 1)
                    self.assertIn(f"area={area}&amp;category=", topics)
                    self.assertIn("no-store", page.headers["cache-control"])
        self.api.transit.assert_not_called()
        self.api.natal.assert_not_called()
        with patch.dict(CATEGORY_LABELS, {"new-category": "New"}):
            with self.assertRaisesRegex(ValueError, "Kategorien fehlen"):
                validate_mapping()

    def test_default_selection_and_detail_provenance(self):
        page = self.web.get("/knowledge")
        self.assertIn("Bitte einen Eintrag auswählen", page.text)
        self.assertNotIn('id="type.generator"', page.text)
        page = self.web.get(knowledge_url("type", "type.generator"))
        self.assertIn('id="type.generator"', page.text)
        self.assertEqual(page.text.count("<article "), 1)
        self.assertIn("KI-Entwurf", page.text)
        self.assertIn("unbekannt", page.text)
        with self.sessions.begin() as session:
            session.get(KnowledgeEntityText, ("type.generator", "de", "human-design")).review_status = "reviewed"
        page = self.web.get(knowledge_url("type", "type.generator"))
        self.assertIn("KI-Entwurf", page.text)
        self.assertIn("als geprüft markiert", page.text)

    def test_workspace_and_legacy_pages_share_localized_texts(self):
        with self.sessions.begin() as session:
            for entity_id in ("type.generator", "gate.1", "channel.1-8", "circuit.integration"):
                session.add(KnowledgeEntityText(
                    entity_id=entity_id, locale="en", text_kind="human-design",
                    name=f"English {entity_id}", description=f"English description {entity_id}",
                    origin="human", author="Test", authored_at="2026-11-14",
                    review_status="reviewed", metadata_json={},
                ))
        for category, entity_id, legacy in (
            ("type", "type.generator", "/knowledge/entities?category=type"),
            ("gate", "gate.1", "/gates"),
            ("channel", "channel.1-8", "/channels"),
            ("circuit", "circuit.integration", "/channels"),
        ):
            for path in (knowledge_url(category, entity_id), legacy):
                with self.subTest(path=path):
                    page = self.web.get(path, headers={"Accept-Language": "en"})
                    self.assertEqual(page.status_code, 200)
                    self.assertIn(f"English {entity_id}", page.text)
                    self.assertIn(f"English description {entity_id}", page.text)
                    self.assertIn("Inhaltssprache: en", page.text)
                    self.assertIn('aria-label="Wissensbereiche"', page.text)
        page = self.web.get(knowledge_url("authority", "authority.sacral"), headers={"Accept-Language": "en"})
        self.assertIn("Inhaltssprache: de", page.text)
        self.assertIn("Rückfall für en", page.text)
        with self.sessions.begin() as session:
            session.get(UiSettings, 1).catalog_locale = "de"
        page = self.web.get(knowledge_url("type", "type.generator"), headers={"Accept-Language": "en"})
        self.assertNotIn("English type.generator", page.text)
        self.assertIn("Inhaltssprache: de", page.text)
        self.api.transit.assert_not_called()
        self.api.natal.assert_not_called()

    def test_gate_lines_are_numeric_and_bounded(self):
        page = self.web.get(knowledge_url("gate-line"))
        self.assertIn("Bitte zuerst ein Tor auswählen", page.text)
        page = self.web.get(knowledge_url("gate-line", gate=10))
        entries = page.text.split('aria-label="Eintrag auswählen">')[1].split("</nav>")[0]
        self.assertEqual(re.findall(r"entity=gate-line\.10\.(\d)", entries), list("123456"))
        self.assertNotIn("<article ", page.text)
        page = self.web.get(knowledge_url("gate-line", "gate-line.10.2"))
        self.assertEqual(page.text.count("<article "), 1)
        self.assertIn("Noch kein Beschreibungstext", page.text)
        self.assertIn("entity=gate.10", page.text)

    def test_invalid_selections_are_explicit(self):
        for params in (
            {"area": "unknown"}, {"category": "unknown"}, {"area": "basics", "category": "gate"},
            {"entity": "type.generator"}, {"category": "type", "entity": "authority.sacral"},
            {"category": "gate", "entity": "gate.65"}, {"category": "channel", "entity": "gate.1"},
            {"category": "gate-line", "gate": 0}, {"category": "type", "gate": 1},
            {"category": "gate-line", "entity": "gate-line.10.2", "gate": 11},
        ):
            with self.subTest(params=params):
                self.assertEqual(self.web.get("/knowledge", params=params).status_code, 404)

    def test_specialized_views_and_legacy_anchors(self):
        for category, entity, anchor in (
            ("gate", "gate.1", "gate-1"),
            ("channel", "channel.1-8", "channel-1-8"),
            ("circuit", "circuit.integration", "circuit-integration"),
        ):
            page = self.web.get(knowledge_url(category, entity))
            self.assertEqual(page.status_code, 200, page.text)
            self.assertIn(f'id="{anchor}"', page.text)
        page = self.web.get(knowledge_url("circuit", "circuit.integration"))
        self.assertIn("category=channel&amp;entity=channel.", page.text)
        page = self.web.get(knowledge_url("center", "center.Head"))
        self.assertIn("category=gate&amp;entity=gate.64", page.text)
        for path, anchor in (
            ("/gates", "gate-64"), ("/channels", "channel-1-8"),
            ("/knowledge/entities?category=gate-line", "gate-line.64.6"),
        ):
            page = self.web.get(path)
            self.assertEqual(page.status_code, 200)
            self.assertIn(f'id="{anchor}"', page.text)
            self.assertIn('aria-label="Wissensbereiche"', page.text)
        for path in ("/knowledge", "/knowledge/help"):
            self.assertIn('id="center-timeline"', self.web.get(path).text)

    def test_profile_overlay_is_explicit_in_workspace_and_unchanged_on_old_page(self):
        with self.sessions.begin() as session:
            session.add(BirthProfile(
                id="test", name="Test", birth_local=datetime(2000, 1, 1),
                birth_utc=datetime(2000, 1, 1), timezone="Etc/UTC", place="Test", chart_key="test",
            ))
            session.flush()
            session.get(UiSettings, 1).active_profile_id = "test"
        page = self.web.get(knowledge_url("gate", "gate.1"))
        toggle = re.search(r'<input id="catalog-activations"[^>]+>', page.text).group()
        self.assertNotIn("checked", toggle)
        self.assertIn('data-activation-gate="1"', page.text)
        old_page = self.web.get("/gates")
        toggle = re.search(r'<input id="catalog-activations"[^>]+>', old_page.text).group()
        self.assertIn("checked", toggle)
        self.api.natal.assert_not_called()

    def test_empty_category_is_not_storage_failure(self):
        page = self.web.get(knowledge_url("cross"))
        self.assertIn("Noch keine Einträge", page.text)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE hd_entities")
        page = self.web.get(knowledge_url("type"))
        self.assertEqual(page.status_code, 500)
        self.assertIn("Alembic-Migrationen", page.text)
