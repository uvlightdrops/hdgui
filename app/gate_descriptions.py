from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.models import Channel, Gate


MISSING_DESCRIPTION = "Für dieses Tor ist noch keine Beschreibung hinterlegt."


class CatalogMissing(Exception):
    pass


def gate_descriptions(session=None):
    """Read a fresh catalog snapshot; standalone renderers use neutral placeholders."""
    if session is None:
        return {
            number: {"number": number, "name": f"Gate {number}",
                     "description": MISSING_DESCRIPTION, "harmonic": "", "channel": ""}
            for number in range(1, 65)
        }
    try:
        gates = session.scalars(select(Gate)).all()
        channels = session.scalars(select(Channel).order_by(Channel.gate_a, Channel.gate_b)).all()
    except SQLAlchemyError as exc:
        raise CatalogMissing("Kanonische Tore/Kanäle fehlen. Bitte Alembic-Migrationen ausführen.") from exc
    if {gate.number for gate in gates} != set(range(1, 65)) or len(channels) != 36:
        raise CatalogMissing("Kanonische Tore/Kanäle fehlen. Bitte Alembic-Migrationen ausführen.")
    result = {
        gate.number: {"number": gate.number, "name": gate.name,
                      "description": gate.description or MISSING_DESCRIPTION,
                      "harmonic": "", "channel": ""}
        for gate in gates
    }
    for gate in gates:
        related = [channel for channel in channels if gate.number in (channel.gate_a, channel.gate_b)]
        result[gate.number]["harmonic"] = ", ".join(
            str(channel.gate_b if channel.gate_a == gate.number else channel.gate_a)
            for channel in related)
        result[gate.number]["channel"] = "\n".join(
            f"{channel.gate_a}-{channel.gate_b}"
            + (f" {channel.name}" if channel.name else "")
            + (f" - {channel.description}" if channel.description else "")
            for channel in related)
    return result


def gate_tooltip(gate: int, descriptions=None) -> str:
    item = (descriptions if descriptions is not None else gate_descriptions())[gate]
    return f"Gate {gate}: {item['name']}\n{item['description']}\n{item['channel']}".rstrip()
