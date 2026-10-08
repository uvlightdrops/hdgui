import json
from functools import lru_cache
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

from app.transit import GATES, SIGNS, TransitFrame


CENTERS = {
    "Head": (300, 55, (64, 61, 63)),
    "Ajna": (300, 155, (47, 24, 4, 17, 43, 11)),
    "Throat": (300, 270, (62, 23, 56, 35, 12, 45, 33, 8, 31, 20, 16)),
    "G": (300, 400, (1, 13, 25, 46, 2, 15, 10, 7)),
    "Heart": (420, 375, (21, 51, 26, 40)),
    "Spleen": (120, 475, (48, 57, 44, 50, 32, 28, 18)),
    "Solar": (480, 495, (6, 37, 22, 36, 30, 55, 49)),
    "Sacral": (300, 535, (5, 14, 29, 59, 9, 3, 42, 27, 34)),
    "Root": (300, 690, (53, 60, 52, 19, 39, 41, 58, 38, 54)),
}
CHANNELS = (
    (64, 47), (61, 24), (63, 4), (17, 62), (43, 23), (11, 56),
    (31, 7), (8, 1), (33, 13), (20, 10), (16, 48), (20, 57),
    (20, 34), (12, 22), (35, 36), (45, 21), (25, 51), (10, 57),
    (10, 34), (2, 14), (15, 5), (46, 29), (44, 26), (37, 40),
    (27, 50), (34, 57), (59, 6), (42, 53), (3, 60), (9, 52),
    (32, 54), (28, 38), (18, 58), (19, 49), (39, 55), (41, 30),
)


@lru_cache(maxsize=1)
def load_layout():
    return json.loads((Path(__file__).parent / "data/bodygraph_layout.json").read_text())


def draw_center(root, data, attrs):
    if data["type"] == "rect":
        SubElement(root, "rect", {
            "x": str(data["x"]), "y": str(data["y"]),
            "width": str(data["w"]), "height": str(data["h"]), **attrs,
        })
    else:
        if data.get("transform"):
            attrs = {**attrs, "transform": f"matrix({data['transform']})"}
        SubElement(root, "path", {"d": data["path"], **attrs})


def render_gate_walk() -> str:
    layout = load_layout()
    root = Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg", "viewBox": "0 0 240 320",
        "role": "img", "aria-labelledby": "gate-walk-title", "id": "gate-walk-svg",
    })
    SubElement(root, "title", {"id": "gate-walk-title"}).text = "Wanderung durch die Tore in Tierkreis-Reihenfolge"
    for data in layout["centers"].values():
        draw_center(root, data, {"fill": "#1a2635", "stroke": "#60758e", "stroke-width": ".7"})
    for gate in GATES:
        coords = layout["gates_coords"][str(gate)]
        x, y = coords["x"] + 3.2, coords["y"] + 3
        group = SubElement(root, "g", {"data-walk-gate": str(gate)})
        SubElement(group, "circle", {
            "cx": str(x), "cy": str(y), "r": "3", "fill": "#1a2635",
            "stroke": "#60758e", "stroke-width": ".3",
        })
        SubElement(group, "text", {
            "x": str(x), "y": str(y), "font-size": "4", "fill": "#8e9aab",
            "text-anchor": "middle", "dominant-baseline": "middle",
        }).text = str(gate)
    SubElement(root, "polyline", {
        "id": "gate-walk-trail", "fill": "none", "stroke": "#85dacc",
        "stroke-opacity": ".65", "stroke-width": ".8", "stroke-linejoin": "round",
    })
    first = layout["gates_coords"][str(GATES[0])]
    SubElement(root, "circle", {
        "id": "gate-walk-dot", "cx": str(first["x"] + 3.2), "cy": str(first["y"] + 3),
        "r": "2", "fill": "#ffdf9c", "stroke": "#101722", "stroke-width": ".4",
    })
    return tostring(root, encoding="unicode")


def render_walk_zodiac() -> str:
    import math

    root = Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg", "viewBox": "0 0 240 240",
        "role": "img", "aria-labelledby": "walk-zodiac-title", "id": "walk-zodiac-svg",
    })
    SubElement(root, "title", {"id": "walk-zodiac-title"}).text = "Synchrone Gate-Wanderung im Tierkreis"

    def point(angle, radius):
        angle = math.radians(angle - 90)
        return 120 + radius * math.cos(angle), 120 + radius * math.sin(angle)

    for radius in (76, 94, 116):
        SubElement(root, "circle", {"cx": "120", "cy": "120", "r": str(radius),
                                  "fill": "none", "stroke": "#60758e", "stroke-width": ".5"})
    for index, sign in enumerate(SIGNS):
        x1, y1 = point(index * 30, 94)
        x2, y2 = point(index * 30, 116)
        SubElement(root, "line", {"x1": str(x1), "y1": str(y1), "x2": str(x2),
                                 "y2": str(y2), "stroke": "#60758e", "stroke-width": ".5"})
        x, y = point(index * 30 + 15, 105)
        SubElement(root, "text", {"x": str(x), "y": str(y), "font-size": "5",
                                 "text-anchor": "middle", "dominant-baseline": "middle",
                                 "fill": "#e3eaf3"}).text = sign
    for index, gate in enumerate(GATES):
        angle = 302 + (index + .5) * 360 / 64
        x, y = point(angle, 85)
        SubElement(root, "text", {"x": str(x), "y": str(y), "font-size": "4.5",
                                 "text-anchor": "middle", "dominant-baseline": "middle",
                                 "fill": "#8e9aab"}).text = str(gate)
    SubElement(root, "polyline", {
        "id": "walk-zodiac-trail", "fill": "none", "stroke": "#85dacc",
        "stroke-width": "1.5", "stroke-linejoin": "round",
    })
    x, y = point(302 + 360 / 128, 71)
    SubElement(root, "line", {
        "id": "walk-zodiac-radius", "x1": "120", "y1": "120", "x2": str(x), "y2": str(y),
        "stroke": "#ffdf9c", "stroke-width": ".7", "stroke-opacity": ".65",
    })
    SubElement(root, "circle", {
        "id": "walk-zodiac-dot", "cx": str(x), "cy": str(y), "r": "2", "fill": "#ffdf9c",
    })
    return tostring(root, encoding="unicode")


def render_bodygraph(frame: TransitFrame) -> str:
    active = {position.gate for position in frame.positions}
    complete = [pair for pair in CHANNELS if set(pair) <= active]
    gate_centers = {gate: name for name, (_, _, gates) in CENTERS.items() for gate in gates}
    defined = {gate_centers[gate] for pair in complete for gate in pair}
    layout = load_layout()
    root = Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg", "viewBox": "0 0 240 320",
        "role": "img", "aria-labelledby": "bodygraph-title",
    })
    SubElement(root, "title", {"id": "bodygraph-title"}).text = "BodyGraph des aktuellen Transits"
    SubElement(root, "path", {
        "d": layout["body_outline"], "fill": "#e6e6e6", "stroke": "black", "stroke-width": ".8",
    })
    for first, second in CHANNELS:
        if not ({first, second} & active):
            continue
        group = SubElement(root, "g", {
            "data-channel": f"{first}-{second}",
            "data-complete": str(first in active and second in active).lower(),
        })
        for gate in (first, second):
            if gate in active:
                SubElement(group, "path", {
                    "d": layout["channels"][str(gate)]["channel_path"],
                    "fill": "none", "stroke": "black", "stroke-width": "4",
                    "stroke-linecap": "round",
                })
    for name, data in layout["centers"].items():
        is_defined = ("Solar" if name == "SolarPlexus" else name) in defined
        attrs = {"fill": "#d4af37" if is_defined else "white",
                 "stroke": "#b8860b" if is_defined else "gray", "stroke-width": "2"}
        draw_center(root, data, attrs)
    for gate in sorted(active):
        coords = layout["gates_coords"][str(gate)]
        x, y = coords["x"] + 3.2, coords["y"] + 3
        group = SubElement(root, "g", {"data-active-gate": str(gate)})
        SubElement(group, "circle", {
            "cx": str(x), "cy": str(y), "r": "3.5", "fill": "white",
            "stroke": "purple", "stroke-width": ".5",
        })
        SubElement(group, "text", {
            "x": str(x), "y": str(y), "text-anchor": "middle", "dominant-baseline": "middle",
            "font-size": "5", "fill": "black",
        }).text = str(gate)
    return tostring(root, encoding="unicode")
