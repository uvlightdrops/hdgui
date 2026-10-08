import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api_client import ChartClient, ChartServiceError
from app.config import Settings
from app.database import make_database
from app.main import create_app
from app.models import BirthProfile
from app.profiles import ProfileInput


SVG = '<svg xmlns="http://www.w3.org/2000/svg"><text>Chart</text></svg>'


class FakeCharts:
    def __init__(self):
        self.calls = 0
        self.fail = False

    def render(self, profile):
        self.calls += 1
        if self.fail:
            raise ChartServiceError("Berechnung fehlgeschlagen.")
        return SVG


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.url = f"sqlite:///{Path(self.temp.name) / 'test.db'}"
        self.settings = Settings(database_url=self.url, session_secret="test-only")
        # Apply the actual migration to a disposable database.
        import os
        with patch.dict(os.environ, {"HDGUI_DATABASE_URL": self.url}):
            command.upgrade(Config("alembic.ini"), "head")
        self.charts = FakeCharts()
        self.client = TestClient(create_app(self.settings, self.charts))
        self.client.__enter__()
        html = self.client.get("/profiles/new").text
        self.csrf = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
        self.data = {
            "csrf": self.csrf, "name": "Testprofil", "birth_local": "1990-07-15T14:30:00",
            "timezone": "Europe/Berlin", "place": "Berlin, Deutschland",
        }

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def profile(self):
        engine, sessions = make_database(self.url)
        with sessions() as session:
            profile = session.scalar(select(BirthProfile))
        engine.dispose()
        return profile

    def create(self):
        response = self.client.post("/profiles", data=self.data, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        return self.profile().id

    def test_persistence_cache_and_invalidation(self):
        profile_id = self.create()
        self.assertEqual(self.profile().birth_utc.hour, 12)
        action = f"/profiles/{profile_id}/chart"
        for _ in range(2):
            self.assertEqual(self.client.post(action, data={"csrf": self.csrf}).text, SVG)
        self.assertEqual(self.charts.calls, 1)
        with TestClient(create_app(self.settings, self.charts)) as restarted:
            self.assertEqual(restarted.get(action + ".svg").text, SVG)
            self.assertIn("Testprofil", restarted.get("/profiles").text)
        renamed = {**self.data, "name": "Neuer Name"}
        self.client.post(f"/profiles/{profile_id}", data=renamed)
        self.assertEqual(self.profile().chart_svg, SVG)
        self.client.post(f"/profiles/{profile_id}", data={**renamed, "birth_local": "1990-07-15T15:30"})
        self.assertIsNone(self.profile().chart_svg)
        self.assertEqual(self.client.get(action + ".svg").status_code, 404)

    def test_error_preserves_cached_svg_and_deletion(self):
        profile_id = self.create()
        action = f"/profiles/{profile_id}/chart"
        self.client.post(action, data={"csrf": self.csrf})
        self.charts.fail = True
        result = self.client.post(action, data={"csrf": self.csrf, "refresh": "1"})
        self.assertEqual(result.status_code, 502)
        self.assertEqual(self.profile().chart_svg, SVG)
        self.client.post(f"/profiles/{profile_id}/delete", data={"csrf": self.csrf})
        self.assertIsNone(self.profile())
        self.assertEqual(self.client.get(action + ".svg").status_code, 404)

    def test_invalid_input_and_csrf(self):
        self.assertEqual(self.client.post("/profiles", data={**self.data, "csrf": ""}).status_code, 403)
        for fields in (
            {"timezone": "Invalid/Zone"},
            {"birth_local": "2024-03-31T02:30"},
            {"birth_local": "2024-10-27T02:30"},
            {"name": "   "},
        ):
            self.assertEqual(self.client.post("/profiles", data={**self.data, **fields}).status_code, 422)
        self.assertIsNone(self.profile())

    def test_engine_version_invalidates_existing_chart(self):
        profile_id = self.create()
        self.client.post(f"/profiles/{profile_id}/chart", data={"csrf": self.csrf})
        settings = Settings(database_url=self.url, engine_version="next", session_secret="test-only")
        with TestClient(create_app(settings, self.charts)) as updated:
            self.assertEqual(updated.get(f"/profiles/{profile_id}/chart.svg").status_code, 404)


class TimeTests(unittest.TestCase):
    def test_fractional_timezone(self):
        data = ProfileInput(name="Nepal", birth_local="2000-01-01T12:00",
                            timezone="Asia/Kathmandu", place="Kathmandu")
        self.assertEqual(data.utc_time().isoformat(), "2000-01-01T06:15:00+00:00")


class ChartClientTests(unittest.TestCase):
    def setUp(self):
        from datetime import datetime
        self.profile = BirthProfile(birth_utc=datetime(2000, 1, 1, 6, 15))
        self.client = ChartClient(Settings(api_token="test-only"))

    def response(self, status, content):
        return httpx.Response(
            status, content=content,
            request=httpx.Request("GET", "http://127.0.0.1:9021/bodygraph"),
        )

    def test_svg_and_utc_parameters(self):
        with patch("app.api_client.httpx.get", return_value=self.response(200, SVG)) as get:
            self.assertEqual(self.client.render(self.profile), SVG)
        params = get.call_args.kwargs["params"]
        self.assertEqual((params["hour"], params["minute"]), (6, 15))
        self.assertEqual((params["latitude"], params["longitude"]), (0, 0))
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer test-only")

    def test_invalid_responses_and_upstream_failure(self):
        for response in (
            self.response(401, "Denied"),
            self.response(200, "<html/>"),
            self.response(200, "not XML"),
            self.response(200, '<!DOCTYPE svg [<!ENTITY x "value">]><svg>&x;</svg>'),
        ):
            with patch("app.api_client.httpx.get", return_value=response):
                with self.assertRaises(ChartServiceError):
                    self.client.render(self.profile)
        with patch("app.api_client.httpx.get", side_effect=httpx.ConnectError("offline")):
            with self.assertRaises(ChartServiceError):
                self.client.render(self.profile)

    def test_missing_token(self):
        with self.assertRaises(ChartServiceError):
            ChartClient(Settings(api_token="")).render(self.profile)


if __name__ == "__main__":
    unittest.main()
