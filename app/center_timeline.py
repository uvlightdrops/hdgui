"""Personal center definition over sampled transits."""
from collections import OrderedDict
from datetime import UTC, datetime
import json
from threading import Lock

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api_client import ChartServiceError
from app.bodygraph import CENTER_MARKERS, render_bodygraph
from app.experiments import SampleBusy, utc_timestamp
from app.models import BirthProfile, UiSettings
from app.natal import ChartUnavailable, CENTER_LABELS, ProfileChanged, TopologyMissing, derive, load_topology


def combined_definition(birth, frame, topology):
    return derive({
        "personality": birth["personality"] + [
            position.model_dump() for position in frame.positions
        ],
        "design": birth["design"],
    }, topology)


def center_timeline_router(
    templates, current_profile, descriptions, natal, sessions, samples,
    *, preferences=None, authorize=None, natal_options=None,
):
    router = APIRouter(prefix="/centers")
    headers = {"Cache-Control": "no-store"}
    graphs = OrderedDict()
    graph_lock = Lock()

    def cached_graph(transit, birth, definition, request):
        catalog = descriptions(request)
        centers = [center["id"] for center in definition["centers"]]
        key = json.dumps([
            transit.model_dump(mode="json"), birth["profile"]["birth_utc"],
            birth["engine_version"], birth["personality"], birth["design"], centers, catalog,
        ], sort_keys=True)
        with graph_lock:
            if key not in graphs:
                graphs[key] = render_bodygraph(
                    transit, catalog, natal=birth, defined_centers=centers,
                )
                while len(graphs) > 64:
                    graphs.popitem(last=False)
            graphs.move_to_end(key)
            return graphs[key]

    @router.get("")
    def page(request: Request):
        return templates.TemplateResponse(
            request=request, name="center_timeline.html",
            context={
                "current_profile": current_profile(request),
                "centers": [
                    {"id": key, "label": label, "color": CENTER_MARKERS[key][2]}
                    for key, label in CENTER_LABELS.items()
                ],
                "start": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M"),
            },
            headers=headers,
        )

    @router.get("/frame")
    def frame(request: Request, timestamp: datetime, profile_id: str, graph: bool = False):
        try:
            timestamp = utc_timestamp(timestamp)
            with sessions() as session:
                prefs = preferences(session, request) if preferences else session.get(UiSettings, 1)
                if prefs is None:
                    raise TopologyMissing("Einstellungen fehlen. Bitte Alembic-Migrationen ausführen.")
                if authorize:
                    authorize(session, profile_id, request)
                if prefs.active_profile_id != profile_id:
                    raise ProfileChanged("Das aktuelle Profil wurde gewechselt oder abgewählt.")
                topology = load_topology(session)
            birth = natal.activations(profile_id, **(natal_options(request, profile_id) if natal_options else {}))
            transit = samples.sample(timestamp, wait=False)
            definition = combined_definition(birth, transit, topology)
            # Do not attach results to a profile changed while the API request was in flight.
            with sessions() as session:
                prefs = preferences(session, request) if preferences else session.get(UiSettings, 1)
                profile = authorize(session, profile_id, request) if authorize else session.get(BirthProfile, profile_id)
                if (
                    prefs is None or prefs.active_profile_id != profile_id
                    or profile is None
                    or profile.birth_utc.replace(tzinfo=UTC).isoformat() != birth["profile"]["birth_utc"]
                ):
                    raise ProfileChanged("Das aktuelle Profil wurde während der Berechnung geändert.")
            payload = {
                "timestamp": timestamp.isoformat(),
                "profile": birth["profile"],
                "engine_version": birth["engine_version"],
                "calculation_version": samples.namespace,
                "centers": definition["centers"],
                "natal_centers": birth["centers"],
                "channels": definition["channels"],
            }
            if graph:
                payload["svg"] = cached_graph(transit, birth, definition, request)
            return JSONResponse(payload, headers=headers)
        except (ProfileChanged, ChartUnavailable) as exc:
            return JSONResponse(
                {"detail": f"{exc} Bitte die Seite neu laden."}, status_code=409, headers=headers,
            )
        except ChartServiceError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=502, headers=headers)
        except (TopologyMissing, SQLAlchemyError) as exc:
            detail = str(exc) if isinstance(exc, TopologyMissing) else (
                "Profil-Datenbank nicht verfügbar. Bitte Alembic-Migrationen ausführen."
            )
            return JSONResponse({"detail": detail}, status_code=500, headers=headers)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)
        except SampleBusy:
            return JSONResponse(
                {"detail": "Eine Transitberechnung läuft bereits. Bitte kurz warten."},
                status_code=503, headers={**headers, "Retry-After": "1"},
            )

    return router
