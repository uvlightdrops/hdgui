import unittest
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from pathlib import Path
from dataclasses import replace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from app.api_client import ChartServiceError
from app.config import Settings
from app.database import make_database
from app.models import TransitSample
from app.experiments import SampleBusy, SampleCache, timeline_plan, zodiac_position
from app.main import create_app
from app.transit import GATES, TransitFrame, TransitPosition


class ExperimentTests(unittest.TestCase):
    def test_persistent_samples_survive_restart_and_ram_expiry(self):
        engine, sessions = make_database(self.settings.database_url)
        self.addCleanup(engine.dispose)
        timestamp = datetime(2026, 10, 8, tzinfo=UTC)
        cache = SampleCache(self.client, sessions, self.settings)
        with patch("app.experiments.monotonic", return_value=0):
            expected = cache.sample(timestamp)
        with patch("app.experiments.monotonic", return_value=7200):
            self.assertEqual(cache.sample(timestamp), expected)
        restarted = SampleCache(self.client, sessions, self.settings)
        self.assertEqual(restarted.transit(timestamp), expected)
        self.assertEqual(self.client.transit.call_count, 1)
        with sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(TransitSample)), 1)

    def test_persistent_cache_is_namespaced_by_api_and_engine(self):
        engine, sessions = make_database(self.settings.database_url)
        self.addCleanup(engine.dispose)
        timestamp = datetime(2026, 10, 8, tzinfo=UTC)
        for settings in (
            self.settings,
            replace(self.settings, engine_version="new-engine"),
            replace(self.settings, api_url="http://localhost:9999"),
        ):
            SampleCache(self.client, sessions, settings).sample(timestamp)
        self.assertEqual(self.client.transit.call_count, 3)
        SampleCache(self.client, sessions, replace(
            self.settings, api_url=self.settings.api_url + "/", api_token="another-secret",
        )).sample(timestamp)
        self.assertEqual(self.client.transit.call_count, 3)

    def test_persistent_cache_is_bounded_across_namespaces(self):
        engine, sessions = make_database(self.settings.database_url)
        self.addCleanup(engine.dispose)
        timestamp = datetime(2026, 10, 8, tzinfo=UTC)
        with patch("app.experiments.MAX_STORED_SAMPLES", 2):
            for index in range(4):
                SampleCache(self.client, sessions, replace(
                    self.settings, engine_version=str(index),
                )).sample(timestamp)
        with sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(TransitSample)), 2)

    def test_persistent_cache_errors_and_invalid_data_are_explicit(self):
        engine, sessions = make_database(self.settings.database_url)
        self.addCleanup(engine.dispose)
        timestamp = datetime(2026, 10, 8, tzinfo=UTC)
        cache = SampleCache(self.client, sessions, self.settings)
        self.client.transit.side_effect = ChartServiceError("offline")
        with self.assertRaisesRegex(ChartServiceError, "offline"):
            cache.sample(timestamp)
        with sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(TransitSample)), 0)
        self.client.transit.side_effect = lambda value: TransitFrame(
            timestamp_utc=value + timedelta(seconds=1),
            positions=[TransitPosition(body="Sun", longitude=1, gate=1, line=1)],
        )
        with self.assertRaisesRegex(ChartServiceError, "abweichenden"):
            cache.sample(timestamp)
        with sessions.begin() as session:
            session.add(TransitSample(
                namespace=cache.namespace, timestamp_utc=timestamp.replace(tzinfo=None),
                payload="invalid-json", created_at=datetime.now(UTC).replace(tzinfo=None),
            ))
        self.client.transit.reset_mock()
        with self.assertRaisesRegex(ChartServiceError, "Ungültiger Transit-Cache"):
            cache.sample(timestamp)
        self.client.transit.assert_not_called()
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE transit_samples")
        with self.assertRaisesRegex(ChartServiceError, "Alembic-Migrationen"):
            cache.sample(timestamp)

    def test_app_restart_reuses_persistent_samples(self):
        params = {"timestamp": "2026-10-08T12:00:00Z"}
        for _ in range(2):
            with TestClient(create_app(self.settings, self.client)) as web:
                response = web.get("/experiments/frame", params=params)
                self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.transit.call_count, 1)

    def test_duplicate_process_insert_reuses_existing_valid_row(self):
        engine, sessions = make_database(self.settings.database_url)
        self.addCleanup(engine.dispose)
        timestamp = datetime(2026, 10, 8, tzinfo=UTC)
        first = SampleCache(self.client, sessions, self.settings)
        second = SampleCache(self.client, sessions, self.settings)
        frame = first.sample(timestamp)
        second._store(frame)
        self.assertEqual(second.sample(timestamp), frame)
        self.assertEqual(self.client.transit.call_count, 1)
        with sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(TransitSample)), 1)

    def test_cache_migration_downgrade_preserves_existing_data(self):
        engine, sessions = make_database(self.settings.database_url)
        self.addCleanup(engine.dispose)
        timestamp = datetime(2026, 10, 8, tzinfo=UTC)
        SampleCache(self.client, sessions, self.settings).sample(timestamp)
        with patch.dict(os.environ, {"HDGUI_DATABASE_URL": self.settings.database_url}):
            command.downgrade(Config("alembic.ini"), "0004")
            with engine.connect() as connection:
                self.assertEqual(connection.exec_driver_sql("SELECT count(*) FROM gates").scalar(), 64)
                self.assertEqual(connection.exec_driver_sql("SELECT count(*) FROM ui_settings").scalar(), 1)
            command.upgrade(Config("alembic.ini"), "head")
        with sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(TransitSample)), 0)

    def test_gate_walk_needs_no_api(self):
        with tempfile.TemporaryDirectory() as directory:
            url = f"sqlite:///{Path(directory) / 'walk.db'}"
            with patch.dict(os.environ, {"HDGUI_DATABASE_URL": url}):
                command.upgrade(Config("alembic.ini"), "head")
            with TestClient(create_app(Settings(database_url=url), self.client)) as web:
                response = web.get("/experiments/gate-walk")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="gate-walk-trail"', response.text)
        self.assertIn("/static/gate-walk.js", response.text)
        self.client.transit.assert_not_called()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = Settings(database_url=f"sqlite:///{Path(directory.name) / 'experiments.db'}")
        with patch.dict(os.environ, {"HDGUI_DATABASE_URL": self.settings.database_url}):
            command.upgrade(Config("alembic.ini"), "head")
        self.client = Mock()
        self.client.transit.side_effect = lambda timestamp: TransitFrame(
            timestamp_utc=timestamp,
            positions=[TransitPosition(body="Sun", longitude=194.123, gate=57, line=3)],
        )

    def test_zodiac_rounding_and_wrap(self):
        self.assertEqual(zodiac_position(0), "00° Widder 00′")
        self.assertEqual(zodiac_position(194.123), "14° Waage 07′")
        self.assertEqual(zodiac_position(29.9999), "00° Stier 00′")
        self.assertEqual(zodiac_position(359.9999), "00° Widder 00′")

    def test_pages_and_hourly_samples_share_cache(self):
        with TestClient(create_app(self.settings, self.client)) as web:
            for page in ("ephemeris", "series", "gate-plot"):
                response = web.get(f"/experiments/{page}")
                self.assertEqual(response.status_code, 200)
                self.assertIn(f'data-page="{page}"', response.text)
            params = {"start": "2026-10-08T12:00:00Z", "days": 1}
            response = web.get("/experiments/data", params=params)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(len(data["frames"]), 25)
            self.assertEqual(data["step_hours"], 1)
            self.assertEqual(data["gate_order"], list(GATES))
            self.assertEqual(data["frames"][0]["positions"][0]["zodiac"], "14° Waage 07′")
            web.get("/experiments/data", params=params)
            self.assertEqual(self.client.transit.call_count, 25)
            daily = web.get("/experiments/data", params={**params, "days": 30, "daily": True})
            self.assertEqual(len(daily.json()["frames"]), 31)
            self.assertEqual(daily.json()["step_hours"], 24)

    def test_validation_and_errors(self):
        with TestClient(create_app(self.settings, self.client)) as web:
            for params in (
                {"start": "2026-10-08T12:00", "days": 1},
                {"start": "2026-10-08T12:00Z", "days": 365},
                {"start": "1800-10-08T12:00Z", "days": 1},
            ):
                self.assertEqual(web.get("/experiments/data", params=params).status_code, 422)
            self.assertEqual(self.client.transit.call_count, 0)
            self.client.transit.side_effect = ChartServiceError("Dienst offline")
            response = web.get("/experiments/data", params={"start": "2026-10-08T12:00Z"})
            self.assertEqual(response.status_code, 502)
            self.assertIn("Dienst offline", response.text)
            self.assertEqual(web.get("/experiments/invalid").status_code, 404)

    def test_cache_expires_and_is_bounded(self):
        cache = SampleCache(self.client)
        timestamp = datetime(2026, 10, 8, tzinfo=UTC)
        with patch("app.experiments.monotonic", return_value=0):
            first = cache.sample(timestamp)
        with patch("app.experiments.monotonic", return_value=3599):
            self.assertIs(cache.sample(timestamp), first)
        with patch("app.experiments.monotonic", return_value=3600):
            self.assertIsNot(cache.sample(timestamp), first)
        self.assertEqual(self.client.transit.call_count, 2)

    def test_plan_is_adaptive_bounded_and_never_calculates(self):
        with TestClient(create_app(self.settings, self.client)) as web:
            for start, end, samples in (
                ("2026-10-08T12:00:00Z", "2026-10-09T12:00:00Z", 64),
                ("1900-01-01T00:00:00Z", "2099-12-31T23:59:59Z", 160),
                ("2026-10-08T12:00:00Z", "2026-10-08T12:01:00Z", 16),
            ):
                response = web.get("/experiments/timeline", params={"start": start, "end": end, "samples": samples})
                self.assertEqual(response.status_code, 200)
                plan = response.json()
                self.assertLessEqual(len(plan["timestamps"]), samples)
                self.assertEqual(plan["timestamps"][0], start.replace("Z", "+00:00"))
                self.assertEqual(plan["timestamps"][-1], end.replace("Z", "+00:00"))
                self.assertEqual(plan["gate_order"], list(GATES))
                self.assertEqual(plan["timestamps"], sorted(set(plan["timestamps"])))
                step = plan["step_seconds"]
                for timestamp in plan["timestamps"][1:-1]:
                    self.assertEqual(int(datetime.fromisoformat(timestamp).timestamp()) % step, 0)
            self.assertEqual(self.client.transit.call_count, 0)
        day = timeline_plan(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC), 64)
        year = timeline_plan(datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC), 64)
        fine = timeline_plan(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC), 160)
        self.assertGreater(year["step_seconds"], day["step_seconds"])
        self.assertLess(fine["step_seconds"], day["step_seconds"])

    def test_timeline_range_validation(self):
        valid = {"start": "2026-10-08T12:00:00Z", "end": "2026-10-09T12:00:00Z"}
        with TestClient(create_app(self.settings, self.client)) as web:
            for change in (
                {"start": "2026-10-08T12:00:00"},
                {"end": "2026-10-09T12:00:00"},
                {"end": valid["start"]},
                {"end": "2026-10-08T11:00:00Z"},
                {"end": "2026-10-08T12:00:59Z"},
                {"start": "1899-12-31T23:59:59Z"},
                {"end": "2100-01-01T00:00:00Z"},
                {"end": "2100-01-01T00:00:00-01:00"},
                {"samples": 161}, {"samples": 15}, {"samples": "NaN"},
                {"start": "garbage"},
            ):
                self.assertEqual(web.get("/experiments/timeline", params={**valid, **change}).status_code, 422, change)
            for timestamp in ("2026-01-01T00:00:00", "1899-01-01T00:00Z", "2100-01-01T00:00Z"):
                self.assertEqual(web.get("/experiments/frame", params={"timestamp": timestamp}).status_code, 422)
            self.assertEqual(web.get("/experiments/data", params={"start": "2099-12-31T00:00Z"}).status_code, 422)
            self.assertEqual(self.client.transit.call_count, 0)

    def test_frame_returns_all_bodies_and_shares_normalized_cache(self):
        self.client.transit.side_effect = lambda timestamp: TransitFrame(
            timestamp_utc=timestamp,
            positions=[TransitPosition(body=body, longitude=index * 20, gate=GATES[index], line=1)
                       for index, body in enumerate(("Sun", "Moon", "Earth", "Mercury", "Venus", "Mars", "Jupiter",
                                                   "Saturn", "Uranus", "Neptune", "Pluto", "North_Node", "South_Node", "Extra"))],
        )
        with TestClient(create_app(self.settings, self.client)) as web:
            response = web.get("/experiments/frame", params={"timestamp": "2026-10-08T14:00:00.123+02:00", "body": "Sun"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json()["positions"]), 14)
            self.assertEqual(response.json()["timestamp_utc"], "2026-10-08T12:00:00Z")
            self.assertEqual(response.headers["cache-control"], "no-store")
            web.get("/experiments/frame", params={"timestamp": "2026-10-08T12:00:00Z"})
            self.assertEqual(self.client.transit.call_count, 1)
            web.get("/experiments/data", params={"start": "2026-10-08T12:00:00Z", "daily": True})
            self.assertEqual(self.client.transit.call_count, 2)

    def test_frame_errors_are_not_cached(self):
        self.client.transit.side_effect = ChartServiceError("Dienst offline")
        with TestClient(create_app(self.settings, self.client)) as web:
            for _ in range(2):
                response = web.get("/experiments/frame", params={"timestamp": "2026-10-08T12:00:00Z"})
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json()["detail"], "Dienst offline")
            self.assertEqual(self.client.transit.call_count, 2)

    def test_cache_eviction_and_concurrent_nonblocking_loads(self):
        cache = SampleCache(self.client)
        timestamp = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(513):
            cache.sample(timestamp + timedelta(minutes=i))
        self.assertEqual(len(cache._frames), 512)
        self.assertNotIn(timestamp, cache._frames)
        cached = timestamp + timedelta(minutes=1)
        entered, release = Event(), Event()
        original = self.client.transit.side_effect

        def slow(value):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(value)

        self.client.transit.side_effect = slow
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(cache.sample, timestamp)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(cache.sample(cached, wait=False).timestamp_utc, cached)
                with self.assertRaises(SampleBusy):
                    cache.sample(timestamp, wait=False)
            finally:
                release.set()
            future.result(timeout=5)
        self.assertEqual(cache.sample(timestamp, wait=False).timestamp_utc, timestamp)

    def test_frame_reports_busy_without_waiting(self):
        entered, release = Event(), Event()
        original = self.client.transit.side_effect

        def slow(value):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(value)

        self.client.transit.side_effect = slow
        with TestClient(create_app(self.settings, self.client)) as web:
            with ThreadPoolExecutor(max_workers=1) as pool:
                first = pool.submit(web.get, "/experiments/frame", params={"timestamp": "2026-10-08T12:00Z"})
                try:
                    self.assertTrue(entered.wait(5))
                    busy = web.get("/experiments/frame", params={"timestamp": "2026-10-08T13:00Z"})
                    self.assertEqual(busy.status_code, 503)
                    self.assertEqual(busy.headers["retry-after"], "1")
                    self.assertEqual(web.get("/experiments/gate-plot").status_code, 200)
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=5).status_code, 200)
