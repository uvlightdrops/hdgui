"""Persist and expose a validated individual-chart snapshot, not an interpretation."""
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.config import Settings
from app.hd_catalog import CrossStructure, ensure_cross
from app.models import BirthChart, HdEntity
from app.natal_details import NatalDetails

if TYPE_CHECKING:
    from app.natal import NatalActivations

LABEL_LINKS = {
    "energy_type": {
        "Generator": "type.generator", "Manifesting Generator": "type.manifesting-generator",
        "Projector": "type.projector", "Manifestor": "type.manifestor", "Reflector": "type.reflector",
    },
    "strategy": {
        "Wait to Respond": "strategy.respond", "To Respond": "strategy.respond",
        "Wait for the Invitation": "strategy.invitation", "Wait for Invitation": "strategy.invitation",
        "To Inform": "strategy.inform", "Inform": "strategy.inform",
        "Wait a Lunar Cycle": "strategy.lunar-cycle", "Wait for a Lunar Cycle": "strategy.lunar-cycle",
    },
    "signature": {
        "Satisfaction": "signature.satisfaction", "Success": "signature.success",
        "Peace": "signature.peace", "Surprise": "signature.surprise",
    },
    "not_self": {
        "Frustration": "not-self.frustration", "Bitterness": "not-self.bitterness",
        "Anger": "not-self.anger", "Disappointment": "not-self.disappointment",
    },
    "aura": {
        "Open & Enveloping": "aura.enveloping", "Focused & Absorbing": "aura.focused",
        "Closed & Repelling": "aura.closed", "Resistant & Sampling": "aura.sampling",
    },
    "inner_authority": {
        "Emotional Authority": "authority.emotional", "Solar Plexus": "authority.emotional",
        "Sacral Authority": "authority.sacral", "Splenic Authority": "authority.splenic",
        "Ego Manifested Authority": "authority.ego-manifested", "Ego Projected Authority": "authority.ego-projected",
        "Self Projected Authority": "authority.self-projected", "Self-Projected Authority": "authority.self-projected",
        "Environmental Authority": "authority.environmental", "Mental Authority": "authority.environmental",
        "Lunar Authority": "authority.lunar",
    },
    "definition": {
        "No Definition": "definition.none", "Single Definition": "definition.single",
        "Split Definition": "definition.split", "Triple Split Definition": "definition.triple-split",
        "Quadruple Split Definition": "definition.quadruple-split",
    },
}


def chart_links(details: NatalDetails) -> tuple[dict[str, str], dict[str, str]]:
    """Unknown labels remain explicit rather than guessing an enum or losing raw values."""
    links, unmapped = {}, {}
    for field, labels in LABEL_LINKS.items():
        value = getattr(details, field)
        entity_id = labels.get(value)
        if field == "inner_authority" and value == "No Inner Authority":
            entity_id = "authority.lunar" if details.energy_type == "Reflector" else "authority.environmental"
        if entity_id is None:
            unmapped[field] = value
        else:
            links[field] = entity_id
    links["profile"] = "profile." + details.profile_code.replace("/", "-")
    for slot, variable in details.variables.model_dump(exclude={"short_code"}).items():
        links[f"variables.{slot}.direction"] = "direction." + variable["value"]
    return links, unmapped


def save_birth_chart(
    session: Session, natal: "NatalActivations", derived: dict, gate_centers: dict[int, str], profile_id: str,
    key: str, settings: Settings, calculated_at: datetime,
) -> BirthChart:
    """Called in the same transaction as the activation replacement."""
    details, raw_response = natal.require_complete()
    if set(details.defined_centers) != {center["id"] for center in derived["centers"]}:
        raise ValueError("API-Zentren widersprechen den vollständigen Geburtskanälen.")
    links, unmapped = chart_links(details)
    for entity_id in links.values():
        if session.get(HdEntity, entity_id) is None:
            raise ValueError("Entitätenkatalog unvollständig. Bitte Migrationen ausführen.")
    personality = {position.body: position for position in natal.personality}
    design = {position.body: position for position in natal.design}
    angle = "juxtaposition" if details.profile_code == "4/1" else (
        "left" if personality["Sun"].line >= 5 else "right"
    )
    cross = ensure_cross(session, CrossStructure(
        angle=angle, gates=[personality["Sun"].gate, personality["Earth"].gate,
                            design["Sun"].gate, design["Earth"].gate],
    ), details.inc_cross)
    links["incarnation_cross"] = cross.id
    chart = session.get(BirthChart, profile_id)
    if chart is None:
        chart = BirthChart(profile_id=profile_id)
        session.add(chart)
    chart.schema_version = 1
    chart.calculation_key, chart.engine_version = key, settings.engine_version
    chart.api_endpoint, chart.calculated_at = "/calculate", calculated_at
    chart.birth_utc = details.birth_date.replace(tzinfo=None)
    chart.design_utc = details.create_date.replace(tzinfo=None)
    chart.type_id, chart.authority_id = links.get("energy_type"), links.get("inner_authority")
    chart.hd_profile_id, chart.definition_id = links["profile"], links.get("definition")
    chart.incarnation_cross_id = cross.id
    chart.general = details.model_dump(mode="json", exclude={"variables", "defined_centers", "undefined_centers"})
    chart.variables = details.variables.model_dump(mode="json")
    active_gates = {position.gate for position in (*natal.personality, *natal.design)}
    activated_centers = {gate_centers[gate] for gate in active_gates}
    chart.centers = {
        "defined": details.defined_centers, "undefined": details.undefined_centers,
        "open": [center for center in details.undefined_centers if center not in activated_centers],
    }
    chart.channels = [{"gates": channel["gates"], "centers": channel["centers"]} for channel in derived["channels"]]
    chart.catalog_links, chart.unmapped_values = links, unmapped
    chart.raw_response = raw_response
    session.flush()
    return chart


def chart_payload(chart: BirthChart) -> dict:
    return {
        "schema_version": chart.schema_version,
        "calculation_key": chart.calculation_key,
        "engine_version": chart.engine_version,
        "engine_version_source": "configured",
        "api_endpoint": chart.api_endpoint,
        "calculated_at": chart.calculated_at.replace(tzinfo=UTC).isoformat(),
        "birth_utc": chart.birth_utc.replace(tzinfo=UTC).isoformat(),
        "design_utc": chart.design_utc.replace(tzinfo=UTC).isoformat(),
        "general": chart.general, "variables": chart.variables,
        "centers": chart.centers, "channels": chart.channels,
        "catalog_links": chart.catalog_links, "unmapped_values": chart.unmapped_values,
        "raw_response": chart.raw_response,
        "raw_response_origin": "humandesign_api; not locally authored or reviewed",
    }
