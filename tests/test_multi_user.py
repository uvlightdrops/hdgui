import os
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from unittest.mock import Mock, patch

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.accounts import AccountsConfig, TestMailSink
from app.config import Settings
from app.database import make_database
from app.main import create_app
from app.models import BirthProfile, ContactRequest, User, UserPreferences


class MultiUserTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.url = f"sqlite:///{Path(directory.name) / 'users.db'}"
        with patch.dict(os.environ, {"HDGUI_DATABASE_URL": self.url}):
            command.upgrade(Config("alembic.ini"), "head")
        self.engine, self.sessions = make_database(self.url)
        self.addCleanup(self.engine.dispose)
        self.mail = TestMailSink()
        self.api = Mock()
        config = AccountsConfig(enabled=True, public_base_url="https://localhost", test_mail_sink=True)
        self.app = create_app(Settings(database_url=self.url, api_url="https://engine.example"),
                              self.api, accounts_config=config, mailer=self.mail)
        self.clients = [TestClient(self.app, base_url="https://localhost") for _ in range(3)]
        for index, client in enumerate(self.clients):
            self.app.state.account_service.request_login(f"user{index}@example.org")
            link = re.search(r"https://localhost/auth/confirm\?token=\S+", self.mail.messages[-1][2]).group()
            page = client.get(link)
            csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
            raw = parse_qs(urlsplit(link).query)["token"][0]
            response = client.post("/auth/confirm", data={"token": raw, "csrf": csrf}, follow_redirects=False)
            self.assertEqual(response.status_code, 303, response.text)
            client.get("/profiles")
        self.owner, self.reader, self.stranger = self.clients
        with self.sessions() as session:
            self.users = [session.scalar(select(User).where(User.email_key == f"user{i}@example.org"))
                          for i in range(3)]
        self.profile_id = self.create_profile(self.owner, "Owner profile")

    def post(self, client, path, data=None):
        response = client.post(path, data={"csrf": client.cookies.get("hdgui_csrf"), **(data or {})},
                               follow_redirects=False)
        return response

    def create_profile(self, client, name):
        response = self.post(client, "/profiles", {
            "name": name, "birth_local": "2000-01-01T12:00", "timezone": "Etc/UTC", "place": "Test",
        })
        self.assertEqual(response.status_code, 303, response.text)
        with self.sessions() as session:
            return session.scalar(select(BirthProfile.id).where(BirthProfile.name == name))

    def connect(self):
        response = self.post(self.owner, "/contacts", {"email": "user1@example.org"})
        self.assertEqual(response.status_code, 303, response.text)
        with self.sessions() as session:
            invitation = session.scalar(select(ContactRequest).where(ContactRequest.status == "pending"))
        response = self.post(self.reader, f"/contacts/requests/{invitation.id}/accept")
        self.assertEqual(response.status_code, 303, response.text)

    def share(self):
        self.connect()
        response = self.post(self.owner, f"/profiles/{self.profile_id}/sharing", {
            "recipient_id": self.users[1].id, "consent": "yes",
        })
        self.assertEqual(response.status_code, 303, response.text)

    def test_private_ownership_and_multiple_profiles(self):
        for i in range(3):
            self.create_profile(self.owner, f"Extra {i}")
        self.create_profile(self.reader, "Reader private")
        self.assertIn("Owner profile", self.owner.get("/profiles").text)
        self.assertNotIn("Reader private", self.owner.get("/profiles").text)
        self.assertNotIn("Owner profile", self.reader.get("/profiles").text)
        for suffix in ("edit", "chart", "chart.svg", "analysis", "sharing"):
            self.assertEqual(self.stranger.get(f"/profiles/{self.profile_id}/{suffix}").status_code, 404)
        for suffix in ("delete", "chart", "analysis", "current"):
            self.assertEqual(self.post(self.stranger, f"/profiles/{self.profile_id}/{suffix}").status_code, 404)
        self.assertEqual(TestClient(self.app).get("/profiles").status_code, 401)
        self.assertEqual(self.owner.post("/profiles", data={}).status_code, 403)
        self.api.natal.assert_not_called()
        self.api.render.assert_not_called()

    def test_login_form_and_request_size_limit(self):
        client = TestClient(self.app, base_url="https://localhost")
        page = client.get("/auth/login")
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        response = client.post("/auth/login", data={"email": "new@example.org", "csrf": csrf})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.mail.messages[-1][0], "new@example.org")
        response = client.post("/auth/login", content=b"x" * 65537)
        self.assertEqual(response.status_code, 413)

    def test_read_only_share_and_revocation(self):
        self.share()
        self.assertNotIn("Owner profile", self.reader.get("/profiles").text)
        shared = self.reader.get("/profiles?scope=shared")
        self.assertIn("Owner profile", shared.text)
        self.assertIn("Nur Lesen", shared.text)
        self.assertNotIn(f'/profiles/{self.profile_id}/delete', shared.text)
        self.assertNotIn(f'/profiles/{self.profile_id}/chart"', shared.text)
        self.assertEqual(self.post(self.reader, f"/profiles/{self.profile_id}/current").status_code, 303)
        self.assertEqual(self.reader.get("/profiles/current/overlay").status_code, 409)
        self.api.natal.assert_not_called()
        for suffix in ("delete", "analysis", "chart"):
            self.assertEqual(self.post(self.reader, f"/profiles/{self.profile_id}/{suffix}").status_code, 404)
        self.assertEqual(self.post(self.reader, f"/profiles/{self.profile_id}").status_code, 404)
        response = self.post(self.owner, f"/profiles/{self.profile_id}/sharing/{self.users[1].id}/revoke")
        self.assertEqual(response.status_code, 303)
        self.assertNotIn("Owner profile", self.reader.get("/profiles?scope=shared").text)
        with self.sessions() as session:
            self.assertIsNone(session.get(UserPreferences, self.users[1].id).current_profile_id)
        self.assertEqual(self.reader.get(f"/profiles/{self.profile_id}/analysis").status_code, 404)

    def test_contact_does_not_grant_access_and_removal_revokes(self):
        self.connect()
        self.assertNotIn("Owner profile", self.reader.get("/profiles?scope=shared").text)
        self.assertEqual(self.reader.get(f"/profiles/{self.profile_id}/edit").status_code, 404)
        response = self.post(self.owner, f"/profiles/{self.profile_id}/sharing", {
            "recipient_id": self.users[1].id, "consent": "yes",
        })
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.post(self.reader, f"/contacts/{self.users[0].id}/remove").status_code, 303)
        self.assertNotIn("Owner profile", self.reader.get("/profiles?scope=shared").text)

    def test_preferences_and_stored_svg_are_isolated(self):
        other = self.create_profile(self.reader, "Other profile")
        self.post(self.owner, f"/profiles/{self.profile_id}/current")
        self.post(self.reader, f"/profiles/{other}/current")
        self.post(self.owner, "/settings", {"cache_seconds": "60", "catalog_locale": "en",
                                          "center_timeline_samples": "32"})
        with self.sessions() as session:
            first = session.get(UserPreferences, self.users[0].id)
            second = session.get(UserPreferences, self.users[1].id)
            self.assertEqual(first.current_profile_id, self.profile_id)
            self.assertEqual(second.current_profile_id, other)
            self.assertEqual(first.locale, "en")
            self.assertIsNone(second.locale)
        self.share()
        from app.profiles import chart_key
        with self.sessions.begin() as session:
            profile = session.get(BirthProfile, self.profile_id)
            profile.chart_svg = '<svg xmlns="http://www.w3.org/2000/svg"></svg>'
            profile.chart_generated_at = datetime(2026, 1, 1)
            profile.chart_key = chart_key(profile.birth_utc, "https://engine.example", "4.0.2")
        response = self.reader.get(f"/profiles/{self.profile_id}/chart.svg")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.stranger.get(f"/profiles/{self.profile_id}/chart.svg").status_code, 404)

    def test_shared_cached_overlay_and_owner_changes(self):
        from test_natal import natal
        self.api.natal.return_value = natal()
        self.share()
        self.post(self.owner, f"/profiles/{self.profile_id}/current")
        self.assertEqual(self.owner.get("/profiles/current/overlay").status_code, 200)
        self.api.natal.assert_called_once()
        self.post(self.reader, f"/profiles/{self.profile_id}/current")
        response = self.reader.get("/profiles/current/overlay")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["profile"]["id"], self.profile_id)
        self.assertEqual(response.json()["personality"][0]["gate"], 64)
        self.api.natal.assert_called_once()
        response = self.post(self.owner, f"/profiles/{self.profile_id}", {
            "name": "Updated owner profile", "birth_local": "2000-01-01T12:00",
            "timezone": "Etc/UTC", "place": "Test",
        })
        self.assertEqual(response.status_code, 303)
        self.assertIn("Updated owner profile", self.reader.get("/profiles?scope=shared").text)
        self.post(self.owner, f"/profiles/{self.profile_id}", {
            "name": "Updated owner profile", "birth_local": "2000-01-02T12:00",
            "timezone": "Etc/UTC", "place": "Test",
        })
        self.assertEqual(self.reader.get("/profiles/current/overlay").status_code, 409)
        self.api.natal.assert_called_once()

    def test_logout_during_natal_calculation_prevents_persistence(self):
        from app.models import BirthActivation
        from test_natal import natal
        self.post(self.owner, f"/profiles/{self.profile_id}/current")
        service = self.app.state.account_service
        current = service.authenticate(self.owner.cookies.get("hdgui_session"))

        def calculate(_):
            service.logout(current, all_sessions=True)
            return natal()

        self.api.natal.side_effect = calculate
        response = self.owner.get("/profiles/current/overlay")
        self.assertEqual(response.status_code, 401, response.text)
        with self.sessions() as session:
            self.assertIsNone(session.get(BirthProfile, self.profile_id).activation_key)
            self.assertIsNone(session.scalar(select(BirthActivation).where(
                BirthActivation.profile_id == self.profile_id,
            )))

    def test_revocation_after_cached_read_discards_response(self):
        from app.natal import NatalService
        from app.sharing import revoke
        from test_natal import natal
        self.api.natal.return_value = natal()
        self.share()
        self.post(self.owner, f"/profiles/{self.profile_id}/current")
        self.owner.get("/profiles/current/overlay")
        self.post(self.reader, f"/profiles/{self.profile_id}/current")
        original = NatalService._payload

        def read_then_revoke(service, session, profile):
            payload = original(service, session, profile)
            with self.sessions.begin() as mutation:
                revoke(mutation, self.profile_id, self.users[1].id)
            return payload

        with patch.object(NatalService, "_payload", read_then_revoke):
            response = self.reader.get("/profiles/current/overlay")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertNotIn("Owner profile", response.text)


if __name__ == "__main__":
    unittest.main()
