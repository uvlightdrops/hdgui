from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from math import ceil
from threading import Lock
from time import monotonic

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.api_client import ChartServiceError
from app.bodygraph import render_gate_walk, render_walk_zodiac
from app.gate_descriptions import gate_descriptions
from app.transit import GATES, SIGNS

MIN_TIME = datetime(1900, 1, 1, tzinfo=UTC)
MAX_TIME = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)
MAX_SAMPLES = 160


def utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(422, "Der Zeitpunkt benötigt einen UTC-Offset.")
    try:
        value = value.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise HTTPException(422, "Ungültiger UTC-Zeitpunkt.") from exc
    if not MIN_TIME <= value <= MAX_TIME:
        raise HTTPException(422, "Zeitpunkte müssen zwischen 1900 und 2099 liegen.")
    return value.replace(microsecond=0)


def timeline_plan(start: datetime, end: datetime, samples: int) -> dict:
    start, end = utc_timestamp(start), utc_timestamp(end)
    span = (end - start).total_seconds()
    if span < 60:
        raise HTTPException(422, "Das Ende muss mindestens eine Minute nach dem Start liegen.")
    if not 16 <= samples <= MAX_SAMPLES:
        raise HTTPException(422, f"Erlaubt sind 16 bis {MAX_SAMPLES} Abtastpunkte.")
    # An epoch-aligned grid allows overlapping viewports to reuse cached samples.
    minimum_step = span / (samples - 2)
    steps = (60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600,
             43200, 86400, 172800, 432000, 864000, 2592000, 7776000,
             15552000, 31536000, 63072000)
    step = next((s for s in steps if s >= minimum_step),
                ceil(minimum_step / 86400) * 86400)
    epoch_start, epoch_end = int(start.timestamp()), int(end.timestamp())
    first = (epoch_start // step + 1) * step
    timestamps = [start]
    timestamps.extend(datetime.fromtimestamp(t, UTC)
                      for t in range(first, epoch_end, step))
    timestamps.append(end)
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "timestamps": [t.isoformat() for t in timestamps],
        "step_seconds": step, "max_samples": MAX_SAMPLES,
        "gate_order": GATES,
        "limits": {"start": MIN_TIME.isoformat(), "end": MAX_TIME.isoformat()},
    }


class SampleBusy(Exception):
    pass


def zodiac_position(longitude: float) -> str:
    minutes = int((longitude % 360) * 60 + .5) % (360 * 60)
    sign, remainder = divmod(minutes, 30 * 60)
    degrees, arcminutes = divmod(remainder, 60)
    return f"{degrees:02d}° {SIGNS[sign]} {arcminutes:02d}′"


class SampleCache:
    def __init__(self, client):
        self.client = client
        self._lock = Lock()
        self._compute_lock = Lock()
        self._frames = OrderedDict()

    def _cached(self, timestamp):
        with self._lock:
            entry = self._frames.get(timestamp)
            if entry is not None and monotonic() - entry[0] < 3600:
                self._frames.move_to_end(timestamp)
                return entry[1]
        return None

    def sample(self, timestamp, *, wait=True):
        timestamp = timestamp.astimezone(UTC).replace(microsecond=0)
        cached = self._cached(timestamp)
        if cached is not None:
            return cached
        if not self._compute_lock.acquire(blocking=wait):
            raise SampleBusy()
        try:
            cached = self._cached(timestamp)
            if cached is not None:
                return cached
            frame = self.client.transit(timestamp)
            with self._lock:
                self._frames[timestamp] = (monotonic(), frame)
                self._frames.move_to_end(timestamp)
                while len(self._frames) > 512:
                    self._frames.popitem(last=False)
            return frame
        finally:
            self._compute_lock.release()


def experiment_router(client, templates, current_profile=lambda: None, descriptions=gate_descriptions):
    router = APIRouter(prefix="/experiments")
    cache = SampleCache(client)
    pages = {
        "ephemeris": ("Tierkreis & Ephemeriden", "Positionen als Tierkreiszeichen mit Grad und Minuten."),
        "series": ("Transit-Zeitreihe", "Positionen, Gates und Linien im zeitlichen Verlauf."),
        "gate-plot": ("Gate-Verlauf", "Alle Himmelskörper im zeitlichen Verlauf. Frei zoomen, navigieren und vergleichen – neue Zeiträume werden automatisch berechnet."),
    }

    @router.get("/gate-walk")
    def gate_walk(request: Request):
        catalog = descriptions()
        return templates.TemplateResponse(
            request=request, name="gate_walk.html",
            context={"graph": render_gate_walk(catalog), "zodiac": render_walk_zodiac(catalog),
                     "gate_order": GATES, "descriptions": catalog},
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/timeline")
    def timeline(start: datetime, end: datetime, samples: int = Query(64, ge=16, le=MAX_SAMPLES)):
        try:
            plan = timeline_plan(start, end, samples)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                headers={"Cache-Control": "no-store"})
        return JSONResponse(plan, headers={"Cache-Control": "no-store"})

    @router.get("/frame")
    def timeline_frame(timestamp: datetime):
        try:
            timestamp = utc_timestamp(timestamp)
            frame = cache.sample(timestamp, wait=False)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                headers={"Cache-Control": "no-store"})
        except SampleBusy:
            return JSONResponse(
                {"detail": "Eine Berechnung läuft bereits. Bitte kurz warten."},
                status_code=503, headers={"Retry-After": "1", "Cache-Control": "no-store"},
            )
        except ChartServiceError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=502,
                                headers={"Cache-Control": "no-store"})
        return JSONResponse(frame.model_dump(mode="json"), headers={"Cache-Control": "no-store"})

    @router.get("/data")
    def data(start: datetime, days: int = 1, daily: bool = False):
        if start.tzinfo is None or start.utcoffset() is None:
            raise HTTPException(422, "Der Startzeitpunkt benötigt einen UTC-Offset.")
        if days not in (1, 7, 30):
            raise HTTPException(422, "Erlaubte Zeiträume: 1, 7 oder 30 Tage.")
        start = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        utc_timestamp(start)
        utc_timestamp(start + timedelta(days=days))
        hours = 24 if daily or days == 30 else (6 if days == 7 else 1)
        frames = []
        try:
            for offset in range(0, days * 24 + 1, hours):
                frame = cache.sample(start + timedelta(hours=offset))
                item = frame.model_dump(mode="json")
                for position in item["positions"]:
                    position["zodiac"] = zodiac_position(position["longitude"])
                frames.append(item)
        except ChartServiceError as exc:
            raise HTTPException(502, str(exc)) from exc
        return JSONResponse(
            {"frames": frames, "step_hours": hours, "gate_order": GATES},
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/{page}")
    def experiment(request: Request, page: str):
        if page not in pages:
            raise HTTPException(404, "Experiment nicht gefunden.")
        title, description = pages[page]
        return templates.TemplateResponse(
            request=request, name="timeline.html" if page == "gate-plot" else "experiment.html",
            context={"page": page, "title": title, "description": description,
                     "gate_order": GATES,
                     "current_profile": current_profile() if page == "gate-plot" else None,
                     "start": datetime.now(UTC).strftime("%Y-%m-%dT%H:00")},
        )

    return router
