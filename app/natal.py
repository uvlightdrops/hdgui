"""Natal (birth) activations: validation, derived channels/centers and persistence."""
from datetime import UTC, datetime
from threading import Lock

from typing import NamedTuple

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import BirthActivation, BirthProfile, Channel, Gate
from app.profiles import activation_key
from app.transit import TransitPosition

SOURCES = ("personality", "design")
SOURCE_LABELS = {"personality": "Persönlichkeit", "design": "Design"}
BODY_LABELS = {
    "Sun": "Sonne", "Earth": "Erde", "Moon": "Mond", "North_Node": "Nördlicher Mondknoten",
    "South_Node": "Südlicher Mondknoten", "Mercury": "Merkur", "Venus": "Venus", "Mars": "Mars",
    "Jupiter": "Jupiter", "Saturn": "Saturn", "Uranus": "Uranus", "Neptune": "Neptun", "Pluto": "Pluto",
}
CENTER_LABELS = {
    "Head": "Kopf", "Ajna": "Ajna", "Throat": "Kehle", "G": "G-Zentrum", "Heart": "Herz/Ego",
    "Spleen": "Milz", "Solar": "Solarplexus", "Sacral": "Sakral", "Root": "Wurzel",
}


class NatalActivations(BaseModel):
    """Both activation sets of exactly one birth profile, as returned by `/calculate`."""
    personality: list[TransitPosition] = Field(min_length=1)
    design: list[TransitPosition] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent_bodies(self):
        bodies = {}
        for source in SOURCES:
            names = [position.body for position in getattr(self, source)]
            if any(len(name) > 40 for name in names):
                raise ValueError("Unerwartet langer Name eines Himmelskörpers.")
            if len(set(names)) != len(names):
                raise ValueError(f"Doppelter Himmelskörper in {SOURCE_LABELS[source]}.")
            bodies[source] = set(names)
        if bodies["personality"] != bodies["design"]:
            raise ValueError("Persönlichkeit und Design enthalten unterschiedliche Himmelskörper.")
        return self


class TopologyMissing(Exception):
    pass


class Topology(NamedTuple):
    """Canonical gate/channel topology as stored in the `gates` and `channels` tables."""
    gate_centers: dict[int, str]
    channels: tuple[tuple[int, int, str | None], ...]


def load_topology(session) -> Topology:
    gates = session.scalars(select(Gate)).all()
    channels = session.scalars(select(Channel).order_by(Channel.id)).all()
    if len(gates) != 64 or len(channels) != 36:
        raise TopologyMissing("Kanonische Tore/Kanäle fehlen. Bitte Alembic-Migrationen ausführen.")
    return Topology({gate.number: gate.center for gate in gates},
                    tuple((channel.gate_a, channel.gate_b, channel.name) for channel in channels))


def derive(activations: dict[str, list[dict]], topology: Topology) -> dict:
    """Active gates by source, complete channels and defined centers from canonical tables."""
    sources: dict[int, set[str]] = {}
    for source in SOURCES:
        for item in activations[source]:
            sources.setdefault(item["gate"], set()).add(source)
    centers_of = topology.gate_centers
    channels = [channel for channel in topology.channels if {channel[0], channel[1]} <= set(sources)]
    centers = {centers_of[gate] for a, b, _ in channels for gate in (a, b)}
    return {
        "gates": [{"gate": gate, "sources": [s for s in SOURCES if s in sources[gate]],
                   "center": centers_of[gate]} for gate in sorted(sources)],
        "channels": [{"gates": [a, b], "name": name, "centers": [centers_of[a], centers_of[b]]}
                     for a, b, name in channels],
        "centers": [{"id": center, "label": CENTER_LABELS[center]}
                    for center in CENTER_LABELS if center in centers],
    }


class ProfileChanged(Exception):
    pass


def activation_lists(profile) -> dict[str, list[dict]]:
    result = {source: [] for source in SOURCES}
    for item in profile.activations:
        result[item.source].append({"body": item.body, "longitude": item.longitude,
                                    "gate": item.gate, "line": item.line})
    return result


def is_fresh(profile, key: str) -> bool:
    lists = activation_lists(profile)
    return profile.activation_key == key and all(lists[source] for source in SOURCES)


class NatalService:
    """Lazily computes and persists activations; never touches the SVG chart cache."""

    def __init__(self, sessions, client, settings):
        self.sessions = sessions
        self.client = client
        self.settings = settings
        self._lock = Lock()

    def key(self, birth_utc: datetime) -> str:
        return activation_key(birth_utc, self.settings.api_url, self.settings.engine_version)

    def _load(self, session, profile_id):
        return session.get(BirthProfile, profile_id,
                           options=[selectinload(BirthProfile.activations)],
                           populate_existing=True)

    def _payload(self, session, profile) -> dict:
        lists = activation_lists(profile)
        return {
            "profile": {
                "id": profile.id, "name": profile.name,
                "birth_local": profile.birth_local.isoformat(timespec="seconds"),
                "timezone": profile.timezone,
                "birth_utc": profile.birth_utc.replace(tzinfo=UTC).isoformat(),
            },
            "calculated_at": profile.activations_calculated_at.replace(tzinfo=UTC).isoformat(),
            "engine_version": profile.activation_engine_version,
            "personality": lists["personality"],
            "design": lists["design"],
            **derive(lists, load_topology(session)),
        }

    def activations(self, profile_id: str) -> dict:
        """Stored activations if fresh, otherwise one API calculation (may raise)."""
        with self.sessions() as session:
            profile = self._load(session, profile_id)
            if profile is None:
                raise ProfileChanged("Das Profil existiert nicht mehr.")
            if is_fresh(profile, self.key(profile.birth_utc)):
                return self._payload(session, profile)
        # Serialize calculations so concurrent requests reuse one result.
        with self._lock:
            with self.sessions() as session:
                profile = self._load(session, profile_id)
                if profile is None:
                    raise ProfileChanged("Das Profil existiert nicht mehr.")
                birth_utc = profile.birth_utc
                key = self.key(birth_utc)
                load_topology(session)  # fail before any API call if the schema is incomplete
                if is_fresh(profile, key):
                    return self._payload(session, profile)
            natal = self.client.natal(birth_utc)
            with self.sessions.begin() as session:
                profile = self._load(session, profile_id)
                # Recheck inputs: an edit during the API call must not receive stale data.
                if profile is None:
                    raise ProfileChanged("Das Profil wurde während der Berechnung gelöscht.")
                if profile.birth_utc != birth_utc or self.key(profile.birth_utc) != key:
                    raise ProfileChanged("Das Profil wurde während der Berechnung geändert.")
                profile.activations.clear()
                session.flush()
                for source in SOURCES:
                    for position in getattr(natal, source):
                        profile.activations.append(BirthActivation(
                            source=source, body=position.body, longitude=position.longitude,
                            gate=position.gate, line=position.line,
                        ))
                profile.activation_key = key
                profile.activation_engine_version = self.settings.engine_version
                profile.activations_calculated_at = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
                session.flush()
                return self._payload(session, profile)
