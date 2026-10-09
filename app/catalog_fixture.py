"""Explicit, transactional import of a YAML catalog."""
import argparse
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.database import make_database
from app.gate_descriptions import CatalogMissing, gate_descriptions
from app.models import Channel, Circuit, Gate


DEFAULT_PATH = Path("data/fixtures/catalog.yaml")
PACKAGED_DEFAULT_PATH = Path(__file__).parent / "data" / "catalog-default.yaml"
GateNumber = Annotated[int, Field(ge=1, le=64)]
GateName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ChannelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CircuitId = Literal["integration", "centering", "knowing", "ego", "defense", "logic", "sensing"]
CircuitName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class FixtureRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class GateRecord(FixtureRecord):
    number: GateNumber
    name: GateName
    description: Description | None
    center: Annotated[str, StringConstraints(min_length=1)]


class ChannelRecord(FixtureRecord):
    gates: Annotated[list[GateNumber], Field(min_length=2, max_length=2)]
    circuit_id: CircuitId
    name: ChannelName | None
    description: Description | None


class CircuitRecord(FixtureRecord):
    id: CircuitId
    name: CircuitName
    description: Description


class CatalogFixture(FixtureRecord):
    schema_version: Annotated[int, Field(ge=2, le=2)]
    gates: list[GateRecord]
    circuits: list[CircuitRecord]
    channels: list[ChannelRecord]


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of silently dropping values."""

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise ValueError(f"Doppelter YAML-Schlüssel: {key!r}")
                seen.add(key)
            except TypeError as exc:
                raise ValueError("YAML-Schlüssel müssen skalare Werte sein.") from exc
        return super().construct_mapping(node, deep=deep)


def read_fixture(path, centers, pairs, circuit_channels):
    """Validate the complete fixture against the existing immutable DB topology."""
    with Path(path).open(encoding="utf-8-sig") as source:
        try:
            fixture = CatalogFixture.model_validate(yaml.load(source, Loader=UniqueKeyLoader))
        except yaml.YAMLError as exc:
            raise ValueError(f"Ungültiges YAML: {exc}") from exc
    gates = {gate.number: gate for gate in fixture.gates}
    if len(fixture.gates) != 64 or set(gates) != set(range(1, 65)):
        raise ValueError("YAML-Katalog muss alle 64 Tore genau einmal enthalten.")
    circuits = {circuit.id: circuit for circuit in fixture.circuits}
    if len(fixture.circuits) != 7 or set(circuits) != set(circuit_channels):
        raise ValueError("YAML-Katalog muss alle sieben kanonischen Schaltkreise genau einmal enthalten.")
    if len({circuit.name for circuit in fixture.circuits}) != len(fixture.circuits):
        raise ValueError("Schaltkreisnamen müssen eindeutig sein.")
    circuit_by_pair = {}
    for circuit_id, members in circuit_channels.items():
        for pair in members:
            if pair in circuit_by_pair:
                raise ValueError(f"Doppelte Schaltkreiszuordnung in der Datenbank: {pair}")
            circuit_by_pair[pair] = circuit_id
    if set(circuit_by_pair) != pairs:
        raise ValueError("Datenbank-Schaltkreise passen nicht zur kanonischen Kanaltopologie.")
    channels = {}
    for channel in fixture.channels:
        pair = tuple(sorted(channel.gates))
        if pair not in pairs or pair in channels:
            raise ValueError(f"Ungültiger oder doppelter Kanal: {pair}")
        if channel.circuit_id != circuit_by_pair[pair]:
            raise ValueError(f"Kanal {pair}: Schaltkreis widerspricht der kanonischen Zuordnung.")
        channels[pair] = channel
    if set(channels) != pairs or len(channels) != 36:
        raise ValueError("YAML-Katalog muss alle 36 kanonischen Kanäle enthalten.")
    for number, gate in gates.items():
        if gate.center != centers[number]:
            raise ValueError(f"Tor {number}: Zentrum widerspricht der Datenbank.")
    return gates, channels, circuits


def resolve_default_path():
    """Prefer a local private catalog when present, otherwise use the packaged one."""
    return DEFAULT_PATH if DEFAULT_PATH.is_file() else PACKAGED_DEFAULT_PATH


def import_fixture(sessions, path=None):
    """Replace all catalog texts atomically, never topology or profile activation."""
    selected_path = resolve_default_path() if path is None else path
    with sessions.begin() as session:
        gate_descriptions(session)  # Fail explicitly if migrations are missing.
        gates = session.scalars(select(Gate)).all()
        channels = session.scalars(select(Channel)).all()
        circuit_rows = session.scalars(select(Circuit)).all()
        centers = {gate.number: gate.center for gate in gates}
        pairs = {(channel.gate_a, channel.gate_b) for channel in channels}
        circuit_channels = {circuit.id: set() for circuit in circuit_rows}
        for channel in channels:
            if channel.circuit_id not in circuit_channels:
                raise ValueError(f"Unbekannter Schaltkreis in der Datenbank: {channel.circuit_id}")
            circuit_channels[channel.circuit_id].add((channel.gate_a, channel.gate_b))
        gate_texts, channel_texts, circuit_texts = read_fixture(
            selected_path, centers, pairs, circuit_channels,
        )
        for gate in gates:
            record = gate_texts[gate.number]
            gate.name, gate.description = record.name, record.description
        for circuit in circuit_rows:
            record = circuit_texts[circuit.id]
            circuit.name, circuit.description = record.name, record.description
        for channel in channels:
            record = channel_texts[(channel.gate_a, channel.gate_b)]
            channel.name, channel.description = record.name, record.description
    return len(gate_texts), len(channel_texts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path)
    args = parser.parse_args()
    selected_path = resolve_default_path() if args.path is None else args.path
    engine, sessions = make_database(Settings().database_url)
    try:
        gates, channels = import_fixture(sessions, selected_path)
    except (OSError, ValueError, CatalogMissing, SQLAlchemyError) as exc:
        parser.exit(1, f"Katalogimport fehlgeschlagen: {exc}\n")
    finally:
        engine.dispose()
    print(f"Katalog importiert aus {selected_path}: {gates} Tore, {channels} Kanaltexte, 7 Schaltkreise.")


if __name__ == "__main__":
    main()
