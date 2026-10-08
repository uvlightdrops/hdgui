import json
import os
import re
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from unittest.mock import Mock, patch
from xml.etree import ElementTree

import httpx
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.api_client import ChartClient, ChartServiceError
from app.config import Settings
from app.main import create_app
from app.transit import GATES, TransitFrame, TransitPosition, render_wheel
from app.transit_service import TransitCache


class TransitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(database_url=f"sqlite:///{self.temp.name}/test.db", session_secret="test-only")
        with patch.dict(os.environ, {"HDGUI_DATABASE_URL": self.settings.database_url}):
            command.upgrade(Config("alembic.ini"), "head")
        self.timestamp = datetime(2026, 10, 7, 20, 18, 45, tzinfo=UTC)
        self.position = {"Planet": "Sun", "Lon": 194.123, "Gate": 57, "Line": 3}
        self.payload = {"gates": {"prs": {"Planets": [self.position]},
                                  "des": {"Planets": [{"invalid": True}]}}}
        self.client = ChartClient(Settings(api_token="test-only"))

    def response(self, payload):
        return httpx.Response(
            200, content=json.dumps(payload),
            request=httpx.Request("GET", "http://127.0.0.1:9021/calculate"),
        )

    def test_current_positions_only(self):
        with patch("app.api_client.httpx.get", return_value=self.response(self.payload)) as get:
            frame = self.client.transit(self.timestamp)
        self.assertEqual(frame.timestamp_utc, self.timestamp)
        self.assertEqual(len(frame.positions), 1)
        self.assertEqual(frame.positions[0].gate, 57)
        self.assertTrue(get.call_args.args[0].endswith("/calculate"))
        self.assertEqual(get.call_args.kwargs["params"]["second"], 45)
        self.assertEqual(get.call_args.kwargs["params"]["place"], "Etc/UTC")

    def test_rounded_zodiac_boundary(self):
        position = TransitPosition.model_validate({**self.position, "Lon": 360})
        self.assertEqual(position.longitude, 0)

    def test_invalid_payloads(self):
        for payload in ([], {}, {"gates": {"prs": {"Planets": []}}},
                        {"gates": {"prs": {"Planets": [{**self.position, "Lon": 999}]}}}):
            with patch("app.api_client.httpx.get", return_value=self.response(payload)):
                with self.assertRaises(ChartServiceError):
                    self.client.transit(self.timestamp)

    def test_wheel_and_page_without_birth_profile(self):
        frame = TransitFrame(timestamp_utc=self.timestamp,
                             positions=[TransitPosition.model_validate(self.position)])
        root = ElementTree.fromstring(render_wheel(frame))
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertEqual(set(GATES), set(range(1, 65)))
        with patch.object(ChartClient, "transit", return_value=frame):
            with TestClient(create_app(self.settings)) as web:
                response = web.get("/transit")
        self.assertEqual(response.status_code, 200)
        self.assertIn("194.123", response.text)
        self.assertIn("Sun", response.text)
        self.assertIn("07.10.2026 20:18:45 UTC", response.text)
        self.assertIn('class="transit-layout"', response.text)
        self.assertIn('class="transit-table"', response.text)
        self.assertIn("Next Gate", response.text)
        self.assertIn("ETA (UTC)", response.text)
        for column in ("gate", "nextGate", "eta"):
            self.assertIn(f'data-sort="{column}"', response.text)
        self.assertIn("/static/gate-forecast.js", response.text)
        self.assertNotIn("<h1>", response.text)
        self.assertNotIn("<header>", response.text)
        self.assertIn('class="sidebar"', response.text)
        self.assertIn('href="/transit" aria-current="page"', response.text)
        self.assertNotIn("<footer>", response.text)
        self.assertNotIn("Momentaufnahme", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_forecast_endpoint_uses_shared_transit_frame(self):
        frame = TransitFrame(timestamp_utc=self.timestamp,
                             positions=[TransitPosition.model_validate(self.position)])
        result = {
            "changes": [{"body": "Sun", "current_gate": 57, "next_gate": 32,
                         "eta_utc": "2026-10-09T12:00:00Z"}],
            "pending": False, "error": None,
        }
        with patch.object(ChartClient, "transit", return_value=frame) as calculate:
            with patch("app.main.GateForecastService.snapshot", return_value=result) as snapshot:
                with TestClient(create_app(self.settings)) as web:
                    web.get("/transit")
                    response = web.get("/transit/next-gates")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), result)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(calculate.call_count, 1)
        snapshot.assert_called_once_with(frame)

    def test_home_bodygraph_and_separate_profiles(self):
        frame = TransitFrame(timestamp_utc=self.timestamp,
                             positions=[TransitPosition.model_validate(self.position)])
        with patch.object(ChartClient, "transit", return_value=frame) as calculate:
            with TestClient(create_app(self.settings)) as web:
                home = web.get("/")
                wheel = web.get("/transit")
                profiles = web.get("/profiles")
        self.assertEqual(home.status_code, 200)
        self.assertIn('data-active-gate="57"', home.text)
        self.assertNotIn('class="profiles"', home.text)
        self.assertEqual(wheel.status_code, 200)
        self.assertEqual(profiles.status_code, 200)
        self.assertIn('class="profiles"', profiles.text)
        self.assertEqual(calculate.call_count, 1)

    def test_service_error_is_visible(self):
        with patch.object(ChartClient, "transit", side_effect=ChartServiceError("Dienst offline")):
            with TestClient(create_app(self.settings)) as web:
                response = web.get("/transit")
        self.assertEqual(response.status_code, 502)
        self.assertIn("Dienst offline", response.text)

    def test_page_requests_share_cache(self):
        frame = TransitFrame(timestamp_utc=self.timestamp,
                             positions=[TransitPosition.model_validate(self.position)])
        with patch.object(ChartClient, "transit", return_value=frame) as calculate:
            with TestClient(create_app(self.settings)) as web:
                first = web.get("/transit")
                second = web.get("/transit")
        self.assertEqual(first.text, second.text)
        self.assertEqual(calculate.call_count, 1)

    def test_settings_persist_and_control_reload(self):
        frame = TransitFrame(timestamp_utc=self.timestamp,
                             positions=[TransitPosition.model_validate(self.position)])
        with TestClient(create_app(self.settings)) as web:
            html = web.get("/settings").text
            csrf = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
            self.assertEqual(web.post("/settings", data={
                "csrf": "", "cache_seconds": "300", "autoreload": "false",
            }).status_code, 403)
            self.assertEqual(web.post("/settings", data={
                "csrf": csrf, "cache_seconds": "42", "autoreload": "false",
            }).status_code, 422)
            for seconds in (60, 300, 600, 1800, 3600):
                response = web.post("/settings", data={
                    "csrf": csrf, "cache_seconds": str(seconds), "autoreload": "false",
                })
                self.assertEqual(response.status_code, 200)
                self.assertIn(f'value="{seconds}" selected', response.text)
        with patch.object(ChartClient, "transit", return_value=frame):
            with TestClient(create_app(self.settings)) as web:
                self.assertNotIn("/static/transit.js", web.get("/transit").text)
                html = web.get("/settings").text
                self.assertIn('value="3600" selected', html)
                csrf = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
                web.post("/settings", data={
                    "csrf": csrf, "cache_seconds": "600", "autoreload": "true",
                })
                self.assertIn('data-reload-seconds="600"', web.get("/transit").text)

    def test_cache_duration_change_applies_to_existing_frame(self):
        frame = TransitFrame(timestamp_utc=self.timestamp,
                             positions=[TransitPosition.model_validate(self.position)])
        client = Mock()
        client.transit.return_value = frame
        cache = TransitCache(client)
        with patch("app.transit_service.monotonic", return_value=0):
            first = cache.get(300)
        with patch("app.transit_service.monotonic", return_value=120):
            self.assertIs(cache.get(300), first)
            self.assertIsNot(cache.get(60), first)
        self.assertEqual(client.transit.call_count, 2)

    def test_cache_expiration_and_failed_refresh(self):
        frame = TransitFrame(timestamp_utc=self.timestamp,
                             positions=[TransitPosition.model_validate(self.position)])
        client = Mock()
        client.transit.return_value = frame
        cache = TransitCache(client)
        with patch("app.transit_service.monotonic", return_value=0):
            first = cache.get()
        with patch("app.transit_service.monotonic", return_value=59):
            self.assertIs(cache.get(), first)
        self.assertEqual(client.transit.call_count, 1)
        client.transit.side_effect = ChartServiceError("offline")
        with patch("app.transit_service.monotonic", return_value=60):
            with self.assertRaises(ChartServiceError):
                cache.get()
        client.transit.side_effect = None
        with patch("app.transit_service.monotonic", return_value=61):
            self.assertIsNot(cache.get(), first)
        self.assertEqual(client.transit.call_count, 3)

    def test_concurrent_requests_share_refresh(self):
        frame = TransitFrame(timestamp_utc=self.timestamp,
                             positions=[TransitPosition.model_validate(self.position)])
        entered, release = Event(), Event()

        def calculate(timestamp):
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError("Refresh was not released")
            return frame

        client = Mock()
        client.transit.side_effect = calculate
        cache = TransitCache(client)
        with ThreadPoolExecutor(max_workers=4) as pool:
            first = pool.submit(cache.get)
            try:
                self.assertTrue(entered.wait(timeout=5))
                others = [pool.submit(cache.get) for _ in range(3)]
            finally:
                release.set()
            result = first.result(timeout=5)
            for future in others:
                self.assertIs(future.result(timeout=5), result)
        self.assertEqual(client.transit.call_count, 1)
