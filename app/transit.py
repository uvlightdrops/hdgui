import math
from datetime import datetime
from xml.etree.ElementTree import Element, SubElement, tostring

from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.gate_descriptions import gate_tooltip


class TransitPosition(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    body: str = Field(alias="Planet", min_length=1)
    longitude: float = Field(alias="Lon", ge=0, le=360)
    gate: int = Field(alias="Gate", ge=1, le=64)
    line: int = Field(alias="Line", ge=1, le=6)

    @field_validator("longitude")
    @classmethod
    def normalize_rounded_longitude(cls, value: float) -> float:
        # Upstream rounding can turn 359.9999 degrees into 360.000.
        return value % 360


class TransitFrame(BaseModel):
    timestamp_utc: datetime
    positions: list[TransitPosition] = Field(min_length=1)


GATES = (
    41, 19, 13, 49, 30, 55, 37, 63, 22, 36, 25, 17, 21, 51, 42, 3,
    27, 24, 2, 23, 8, 20, 16, 35, 45, 12, 15, 52, 39, 53, 62, 56,
    31, 33, 7, 4, 29, 59, 40, 64, 47, 6, 46, 18, 48, 57, 32, 50,
    28, 44, 1, 43, 14, 34, 9, 5, 26, 11, 10, 58, 38, 54, 61, 60,
)
SIGNS = (
    "Widder", "Stier", "Zwillinge", "Krebs", "Löwe", "Jungfrau",
    "Waage", "Skorpion", "Schütze", "Steinbock", "Wassermann", "Fische",
)


def point(angle: float, radius: float) -> tuple[float, float]:
    radians = math.radians(angle - 90)
    return 400 + radius * math.cos(radians), 400 + radius * math.sin(radians)


def render_wheel(frame: TransitFrame, descriptions=None) -> str:
    # BodyGraph rendering also uses TransitFrame; import its shared palette at render time.
    from app.bodygraph import CENTER_MARKERS, GATE_CENTERS

    root = Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg", "viewBox": "0 0 800 800",
        "role": "img", "aria-label": "Tierkreis mit 64 Gates und aktuellen Planetenpositionen",
    })
    for index, gate in enumerate(GATES):
        start = 302 + index * 360 / 64
        end = start + 360 / 64
        outer_start, outer_end = point(start, 330), point(end, 330)
        inner_end, inner_start = point(end, 290), point(start, 290)
        center = GATE_CENTERS[gate]
        sector = SubElement(root, "path", {
            "d": (
                f"M {outer_start[0]} {outer_start[1]} "
                f"A 330 330 0 0 1 {outer_end[0]} {outer_end[1]} "
                f"L {inner_end[0]} {inner_end[1]} "
                f"A 290 290 0 0 0 {inner_start[0]} {inner_start[1]} Z"
            ),
            "fill": CENTER_MARKERS[center][2], "fill-opacity": ".65",
            "data-zodiac-gate": str(gate), "data-center": center,
        })
        SubElement(sector, "title").text = gate_tooltip(gate, descriptions)
    for radius in (220, 290, 330, 380):
        SubElement(root, "circle", {"cx": "400", "cy": "400", "r": str(radius),
                                  "fill": "none", "stroke": "#60758e"})

    def text(angle, radius, value, size="12", color="#e3eaf3"):
        x, y = point(angle, radius)
        label = SubElement(root, "text", {"x": str(x), "y": str(y), "fill": color,
                                 "font-size": size, "text-anchor": "middle",
                                 "dominant-baseline": "middle"})
        label.text = value
        return label

    def line(angle, inner, outer):
        x1, y1 = point(angle, inner)
        x2, y2 = point(angle, outer)
        SubElement(root, "line", {"x1": str(x1), "y1": str(y1), "x2": str(x2),
                                 "y2": str(y2), "stroke": "#60758e"})

    for index, sign in enumerate(SIGNS):
        line(index * 30, 330, 380)
        text(index * 30 + 15, 355, sign)
    active = {position.gate for position in frame.positions}
    for index, gate in enumerate(GATES):
        angle = 302 + index * 360 / 64
        line(angle, 290, 330)
        label = text(angle + 360 / 128, 310, str(gate),
                     color="#85dacc" if gate in active else "#e3eaf3")
        SubElement(label, "title").text = gate_tooltip(gate, descriptions)
    for index, position in enumerate(frame.positions):
        # Separate label tracks keep close planetary positions readable.
        radius = 130 + (index % 5) * 30
        x, y = point(position.longitude, radius)
        SubElement(root, "circle", {"cx": str(x), "cy": str(y), "r": "4", "fill": "#85dacc"})
        line(position.longitude, radius + 8, 290)
        text(position.longitude, radius - 12, position.body, color="#85dacc")
    return tostring(root, encoding="unicode")
