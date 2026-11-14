"""Presentation-only topic mapping and shareable knowledge navigation."""
from urllib.parse import urlencode

from fastapi import HTTPException

from app.hd_catalog import CATEGORY_LABELS


TOPICS = {
    "basics": ("Grundlagen", (
        "type", "strategy", "signature", "not-self", "aura", "authority", "profile", "body", "source",
    )),
    "bodygraph": ("BodyGraph", (
        "center", "definition", "gate", "channel", "circuit", "line", "gate-line",
    )),
    "variables": ("Variablen", (
        "variable", "direction", "color", "tone", "base", "digestion", "environment",
        "perspective", "motivation", "cognition",
    )),
    "crosses": ("Inkarnationskreuze", ("cross-angle", "cross")),
}
UI_LABELS = {**CATEGORY_LABELS, "gate": "Tore", "channel": "Kanäle", "circuit": "Schaltkreise"}


def validate_mapping():
    keys = [key for _, categories in TOPICS.values() for key in categories]
    if len(keys) != len(set(keys)) or set(keys) != set(CATEGORY_LABELS) | {"gate", "channel", "circuit"}:
        raise ValueError("Wissensnavigation: Kategorien fehlen oder sind mehrfach zugeordnet.")


def topic_for(category):
    for area, (_, categories) in TOPICS.items():
        if category in categories:
            return area
    raise HTTPException(404, "Unbekannte Wissenskategorie.")


def knowledge_url(category, entity=None, gate=None):
    params = {"area": topic_for(category), "category": category}
    if entity is not None:
        params["entity"] = entity
    if gate is not None:
        params["gate"] = gate
    return "/knowledge?" + urlencode(params)


def navigation(area=None, category=None):
    validate_mapping()
    if area is None:
        area = topic_for(category) if category is not None else "basics"
    if area not in TOPICS:
        raise HTTPException(404, "Unbekannter Wissensbereich.")
    label, categories = TOPICS[area]
    if category is None:
        category = categories[0]
    if category not in categories:
        raise HTTPException(404, "Kategorie gehört nicht zu diesem Wissensbereich.")
    return {
        "knowledge_navigation": True,
        "knowledge_area": area,
        "knowledge_topic": label,
        "category": category,
        "categories": UI_LABELS,
        "topic_links": [(key, name, knowledge_url(keys[0])) for key, (name, keys) in TOPICS.items()],
        "category_links": [(key, UI_LABELS[key], knowledge_url(key)) for key in categories],
    }


def entity_order(entity):
    if "number" in entity.structure:
        return ((0, entity.structure["number"]),)
    parts = entity.id.split(".")
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts)
