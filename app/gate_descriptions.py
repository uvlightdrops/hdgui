from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.knowledge_catalog import resolve_texts
from app.models import Channel, Gate


MISSING_DESCRIPTION = "Für dieses Tor ist noch keine Beschreibung hinterlegt."


class CatalogMissing(Exception):
    pass


def gate_descriptions(session=None, locale="de"):
    """Read a fresh catalog snapshot; standalone renderers use neutral placeholders."""
    if session is None:
        return {
            number: {"number": number, "name": f"Gate {number}",
                     "description": MISSING_DESCRIPTION, "text_locale": None,
                     "text_is_fallback": True, "harmonic": "", "channel": ""}
            for number in range(1, 65)
        }
    try:
        gates = session.scalars(select(Gate)).all()
        channels = session.scalars(select(Channel).order_by(Channel.gate_a, Channel.gate_b)).all()
        texts = resolve_texts(
            session,
            {f"gate.{gate.number}" for gate in gates}
            | {f"channel.{channel.gate_a}-{channel.gate_b}" for channel in channels},
            locale,
        )
    except SQLAlchemyError as exc:
        raise CatalogMissing("Kanonische Tore/Kanäle fehlen. Bitte Alembic-Migrationen ausführen.") from exc
    if {gate.number for gate in gates} != set(range(1, 65)) or len(channels) != 36:
        raise CatalogMissing("Kanonische Tore/Kanäle fehlen. Bitte Alembic-Migrationen ausführen.")
    result = {}
    for gate in gates:
        localized = texts.get(f"gate.{gate.number}")
        name = localized.record.name if localized else gate.name
        description = localized.record.description if localized else gate.description
        result[gate.number] = {
            "number": gate.number, "name": name or f"Gate {gate.number}",
            "description": description or MISSING_DESCRIPTION,
            "text_locale": localized.record.locale if localized else None,
            "text_is_fallback": localized.is_fallback if localized else True,
            "harmonic": "", "channel": "",
        }
    for gate in gates:
        related = [channel for channel in channels if gate.number in (channel.gate_a, channel.gate_b)]
        result[gate.number]["harmonic"] = ", ".join(
            str(channel.gate_b if channel.gate_a == gate.number else channel.gate_a)
            for channel in related)
        channel_descriptions = []
        for channel in related:
            localized = texts.get(f"channel.{channel.gate_a}-{channel.gate_b}")
            name = localized.record.name if localized else channel.name
            description = localized.record.description if localized else channel.description
            channel_descriptions.append(
                f"{channel.gate_a}-{channel.gate_b}"
                + (f" {name}" if name else "")
                + (f" - {description}" if description else "")
            )
        result[gate.number]["channel"] = "\n".join(channel_descriptions)
    return result


def gate_tooltip(gate: int, descriptions=None) -> str:
    item = (descriptions if descriptions is not None else gate_descriptions())[gate]
    locale = item.get("text_locale") or "unbekannt"
    fallback = " (Rückfall)" if item.get("text_is_fallback") else ""
    return (
        f"Gate {gate}: {item['name']}\n{item['description']}\n"
        f"Inhaltssprache: {locale}{fallback}\n{item['channel']}"
    ).rstrip()
