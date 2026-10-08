import unittest
from xml.etree import ElementTree

from app.profile_svg import enlarge_gate_labels


class ProfileSvgTests(unittest.TestCase):
    def test_gate_glyphs_enlarge_without_moving_channel_geometry(self):
        svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
        <path id="channel" d="M 0 0 L 20 20"/>
        <g id="text_1"><g transform="translate(20 30) scale(0.04 -0.04)">
        <use href="#digit"/><use href="#digit" transform="translate(63.625 0)"/>
        </g></g></svg>"""
        root = ElementTree.fromstring(enlarge_gate_labels(svg))
        ns = {"s": "http://www.w3.org/2000/svg"}
        self.assertEqual(root.get("viewBox"), "0 0 100 100")
        self.assertEqual(root.find("s:path", ns).get("d"), "M 0 0 L 20 20")
        group = root.find("s:g", ns)
        self.assertIn("scale(2)", group.get("transform"))
        self.assertIn("22.545", group.get("transform"))
        self.assertEqual(group.find("s:g", ns).get("transform"),
                         "translate(20 30) scale(0.04 -0.04)")
