import re
from xml.etree.ElementTree import tostring

from defusedxml import ElementTree


SVG = "{http://www.w3.org/2000/svg}"
NUMBER = r"(-?\d+(?:\.\d+)?)"
LABEL_TRANSFORM = re.compile(
    rf"translate\({NUMBER}\s+{NUMBER}\)\s+scale\({NUMBER}\s+{NUMBER}\)"
)


def enlarge_gate_labels(svg: str) -> str:
    """Enlarge Matplotlib's gate glyphs around their existing visual centers."""
    root = ElementTree.fromstring(svg)
    changed = False
    for group in root.iter(f"{SVG}g"):
        if not re.fullmatch(r"text_\d+", group.get("id", "")):
            continue
        for label in group.findall(f"{SVG}g"):
            match = LABEL_TRANSFORM.fullmatch(label.get("transform", ""))
            glyphs = label.findall(f"{SVG}use")
            if match is None or not glyphs:
                continue
            x, y, sx, sy = map(float, match.groups())
            # Upstream uses DejaVu Sans digits with an advance of 63.625 units.
            center_x = x + len(glyphs) * 63.625 * sx / 2
            center_y = y + 35 * sy
            group.set(
                "transform",
                f"translate({center_x} {center_y}) scale(2) "
                f"translate({-center_x} {-center_y})",
            )
            changed = True
    return tostring(root, encoding="unicode") if changed else svg
