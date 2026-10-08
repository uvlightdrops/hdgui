import math
import unittest
from datetime import UTC, datetime, timedelta
from threading import Event
from unittest.mock import Mock

from app.api_client import ChartServiceError
from app.gate_forecast import GateForecastService, find_changes
from app.transit import GATES, TransitFrame, TransitPosition


class SyntheticEphemeris:
    def __init__(self, start, body, trajectory):
        self.start = start
        self.body = body
        self.trajectory = trajectory
        self.calls = 0

    def transit(self, timestamp):
        self.calls += 1
        longitude = self.trajectory((timestamp - self.start).total_seconds() / 86400) % 360
        gate = GATES[int(((longitude + 58) % 360) / (360 / 64))]
        return TransitFrame(timestamp_utc=timestamp, positions=[
            TransitPosition(body=self.body, longitude=round(longitude, 3), gate=gate, line=1)
        ])


class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 8, tzinfo=UTC)

    def search(self, body, trajectory):
        client = SyntheticEphemeris(self.start, body, trajectory)
        changes = []
        find_changes(client, client.transit(self.start), Event(), changes.append)
        self.assertEqual(len(changes), 1)
        return changes[0]

    def assert_eta(self, change, days):
        delta = (change.eta_utc - self.start - timedelta(days=days)).total_seconds()
        self.assertGreaterEqual(delta, 0)
        self.assertLessEqual(delta, 30)

    def test_forward(self):
        change = self.search("Sun", lambda days: 303 + days)
        self.assertEqual(change.next_gate, 19)
        self.assert_eta(change, 4.625)

    def test_retrograde(self):
        change = self.search("Mercury", lambda days: 303 - days)
        self.assertEqual(change.next_gate, 60)
        self.assert_eta(change, 1)

    def test_station_and_reversal(self):
        change = self.search("Mercury", lambda days: 303 + .3 * days - .3 * days ** 2)
        self.assertEqual(change.next_gate, 60)
        self.assert_eta(change, (.3 + math.sqrt(.09 + 1.2)) / .6)

    def test_zodiac_wrap(self):
        change = self.search("Moon", lambda days: 359 + 10 * days)
        self.assertEqual(change.next_gate, 17)
        self.assert_eta(change, .4875)

    def test_slow_planet(self):
        change = self.search("Pluto", lambda days: 303 + .005 * days)
        self.assertEqual(change.next_gate, 19)
        self.assert_eta(change, 925)

    def test_missing_body_is_an_error(self):
        client = SyntheticEphemeris(self.start, "Sun", lambda days: 303 + days)
        first = client.transit(self.start)
        wrong = SyntheticEphemeris(self.start, "Moon", lambda days: 303 + days)
        with self.assertRaises(ChartServiceError):
            find_changes(wrong, first, Event(), lambda change: None)

    def test_service_shares_work_and_reuses_future_event(self):
        client = SyntheticEphemeris(self.start, "Sun", lambda days: 303 + days)
        service = GateForecastService(client)
        try:
            first = client.transit(self.start)
            service.snapshot(first)
            service._future.result(timeout=10)
            calls = client.calls
            result = service.snapshot(first)
            self.assertFalse(result["pending"])
            self.assertEqual(result["changes"][0]["next_gate"], 19)
            self.assertEqual(client.calls, calls)
            service.snapshot(client.transit(self.start + timedelta(days=1)))
            self.assertEqual(client.calls, calls + 1)
        finally:
            service.close()

    def test_service_reports_upstream_error(self):
        source = SyntheticEphemeris(self.start, "Sun", lambda days: 303 + days)
        client = Mock()
        client.transit.side_effect = ChartServiceError("API offline")
        service = GateForecastService(client)
        try:
            frame = source.transit(self.start)
            service.snapshot(frame)
            service._future.result(timeout=10)
            self.assertEqual(service.snapshot(frame)["error"], "API offline")
        finally:
            service.close()
