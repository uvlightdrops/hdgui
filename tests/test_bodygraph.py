import unittest
from datetime import UTC, datetime
from xml.etree import ElementTree

from app.bodygraph import CENTERS, CHANNELS, render_bodygraph, render_gate_walk
from app.transit import GATES, TransitFrame, TransitPosition


class BodyGraphTests(unittest.TestCase):
    def test_gate_walk_has_ordered_gates_and_no_channels(self):
        root = ElementTree.fromstring(render_gate_walk())
        gates = [int(item.attrib["data-walk-gate"]) for item in root.iter()
                 if "data-walk-gate" in item.attrib]
        self.assertEqual(gates, list(GATES))
        self.assertFalse(any("data-channel" in item.attrib for item in root.iter()))
        ids = {item.get("id") for item in root.iter()}
        self.assertTrue({"gate-walk-dot", "gate-walk-trail"} <= ids)

    def test_only_transit_activations_and_complete_channels(self):
        frame = TransitFrame(timestamp_utc=datetime(2026, 10, 8, tzinfo=UTC), positions=[
            TransitPosition(body="Sun", longitude=0, gate=64, line=1),
            TransitPosition(body="Earth", longitude=180, gate=47, line=1),
            TransitPosition(body="Moon", longitude=10, gate=61, line=1),
        ])
        root = ElementTree.fromstring(render_bodygraph(frame))
        self.assertEqual(root.get("viewBox"), "0 0 240 320")
        texts = list(root.iter("{http://www.w3.org/2000/svg}text"))
        self.assertTrue(all(text.get("font-size") == "5" for text in texts))
        gates = {int(item.attrib["data-active-gate"]) for item in root.iter()
                 if "data-active-gate" in item.attrib}
        self.assertEqual(gates, {64, 47, 61})
        channels = {item.attrib["data-channel"]: item.attrib["data-complete"]
                    for item in root.iter() if "data-channel" in item.attrib}
        self.assertEqual(channels, {"64-47": "true", "61-24": "false"})
        self.assertEqual({gate for _, _, gates in CENTERS.values() for gate in gates},
                         set(range(1, 65)))
        self.assertEqual(len(CHANNELS), 36)
