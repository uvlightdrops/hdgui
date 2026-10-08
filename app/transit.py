import math
from datetime import datetime
from xml.etree.ElementTree import Element, SubElement, tostring

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


def render_wheel(frame: TransitFrame) -> str:
    root = Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg", "viewBox": "0 0 800 800",
        "role": "img", "aria-labelledby": "wheel-title",
    })
    SubElement(root, "title", {"id": "wheel-title"}).text = "Aktueller Transit mit Tierkreis und 64 Gates"
    for radius in (220, 290, 330, 380):
        SubElement(root, "circle", {"cx": "400", "cy": "400", "r": str(radius),
                                  "fill": "none", "stroke": "#60758e"})

    def text(angle, radius, value, size="12", color="#e3eaf3"):
        x, y = point(angle, radius)
        SubElement(root, "text", {"x": str(x), "y": str(y), "fill": color,
                                 "font-size": size, "text-anchor": "middle",
                                 "dominant-baseline": "middle"}).text = value

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
        text(angle + 360 / 128, 310, str(gate),
             color="#85dacc" if gate in active else "#e3eaf3")
    for index, position in enumerate(frame.positions):
        # Separate label tracks keep close planetary positions readable.
        radius = 130 + (index % 5) * 30
        x, y = point(position.longitude, radius)
        SubElement(root, "circle", {"cx": str(x), "cy": str(y), "r": "4", "fill": "#85dacc"})
        line(position.longitude, radius + 8, 290)
        text(position.longitude, radius - 12, position.body, color="#85dacc")
    return tostring(root, encoding="unicode")
