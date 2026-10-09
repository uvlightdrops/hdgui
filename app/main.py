import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload
from starlette.middleware.sessions import SessionMiddleware

from app.api_client import ChartClient, ChartServiceError
from app.config import Settings
from app.database import make_database
from app.gate_forecast import GateForecastService
from app.experiments import experiment_router
from app.gate_descriptions import CatalogMissing, gate_descriptions, gate_tooltip
from app.models import BirthProfile, Channel, Circuit, Gate, UiSettings
from app.natal import (
    BODY_LABELS, CENTER_LABELS, SOURCE_LABELS, NatalService, ProfileChanged, TopologyMissing, activation_lists, derive,
    is_fresh, load_topology,
)
from app.profiles import ProfileInput, activation_key, chart_key
from app.profile_svg import enlarge_gate_labels
from app.transit_service import TransitCache


ROOT = Path(__file__).parent
CACHE_OPTIONS = ((60, "1m"), (300, "5m"), (600, "10m"), (1800, "30m"), (3600, "1h"))


class SettingsInput(BaseModel):
    cache_seconds: Literal[60, 300, 600, 1800, 3600]
    autoreload: bool


def create_app(settings: Settings | None = None, chart_client=None) -> FastAPI:
    settings = settings or Settings()
    engine, sessions = make_database(settings.database_url)
    templates = Jinja2Templates(directory=ROOT / "templates")

    def descriptions():
        with sessions() as session:
            try:
                return gate_descriptions(session)
            except CatalogMissing as exc:
                raise HTTPException(500, str(exc)) from exc

    client = chart_client or ChartClient(settings)
    transit_cache = TransitCache(client, descriptions)
    forecasts = GateForecastService(client)
    natal = NatalService(sessions, client, settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        forecasts.close()
        engine.dispose()

    app = FastAPI(title="HDGUI", lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="strict")
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    def current_profile():
        # Database only: selecting or showing the current profile never calls the API.
        try:
            with sessions() as session:
                preferences = session.get(UiSettings, 1)
                if preferences is None or preferences.active_profile_id is None:
                    return None
                profile = session.get(BirthProfile, preferences.active_profile_id)
                return None if profile is None else {"id": profile.id, "name": profile.name}
        except SQLAlchemyError:
            # The overlay is optional; the transit timeline stays usable without profiles.
            return {"error": "Profil-Datenbank nicht verfügbar. Bitte Alembic-Migrationen ausführen."}

    app.include_router(experiment_router(client, templates, current_profile, descriptions))

    def catalog_context():
        with sessions() as session:
            try:
                load_topology(session)
                circuits = session.scalars(select(Circuit).order_by(Circuit.id)).all()
                gates = session.scalars(select(Gate).order_by(Gate.number)).all()
                channels = session.scalars(select(Channel).order_by(Channel.gate_a, Channel.gate_b)).all()
            except (TopologyMissing, SQLAlchemyError) as exc:
                if isinstance(exc, SQLAlchemyError):
                    raise HTTPException(
                        500, "Kanonische Tore, Kanäle und Schaltkreise fehlen. Bitte Alembic-Migrationen ausführen."
                    ) from exc
                raise HTTPException(500, str(exc)) from exc
            if len(circuits) != 7 or any(channel.circuit_id is None for channel in channels):
                raise HTTPException(
                    500, "Kanonische Schaltkreise fehlen. Bitte Alembic-Migrationen ausführen."
                )
        relations = {gate.number: [] for gate in gates}
        for channel in channels:
            relations[channel.gate_a].append(channel)
            relations[channel.gate_b].append(channel)
        circuit_members = {circuit.id: [] for circuit in circuits}
        for channel in channels:
            circuit_members[channel.circuit_id].append(channel)
        return {"gates": gates, "gate_map": {gate.number: gate for gate in gates},
                "channels": channels, "relations": relations, "center_labels": CENTER_LABELS,
                "circuits": circuits, "circuit_map": {circuit.id: circuit for circuit in circuits},
                "circuit_members": circuit_members}

    @app.get("/gates")
    def gates_page(request: Request):
        return templates.TemplateResponse(
            request=request, name="gates.html",
            context={**catalog_context(), "current_profile": current_profile()},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/channels")
    def channels_page(request: Request):
        return templates.TemplateResponse(
            request=request, name="channels.html", context=catalog_context(),
        )

    def token(request):
        if "csrf" not in request.session:
            request.session["csrf"] = secrets.token_urlsafe(32)
        return request.session["csrf"]

    def check_csrf(request, form):
        supplied = str(form.get("csrf", ""))
        expected = request.session.get("csrf")
        if not expected or not secrets.compare_digest(supplied, expected):
            raise HTTPException(403, "Ungültige Formularsitzung. Bitte die Seite neu laden.")

    def get_profile(session, profile_id):
        profile = session.get(BirthProfile, profile_id)
        if profile is None:
            raise HTTPException(404, "Profil nicht gefunden.")
        return profile

    def form_page(request, values=None, profile_id=None, errors=None, status=200):
        return templates.TemplateResponse(
            request=request, name="profile_form.html",
            context={"values": values or {}, "profile_id": profile_id,
                     "errors": errors or [], "csrf": token(request)},
            status_code=status,
        )

    @app.get("/")
    def index(request: Request):
        with sessions() as session:
            preferences = session.get(UiSettings, 1)
            if preferences is None:
                raise HTTPException(500, "Einstellungen fehlen. Bitte Alembic-Migrationen ausführen.")
        try:
            frame, _ = transit_cache.get(preferences.cache_seconds)
        except ChartServiceError as exc:
            raise HTTPException(502, str(exc)) from exc
        return templates.TemplateResponse(
            request=request, name="home.html",
            context={"frame": frame, "bodygraph": transit_cache.bodygraph(frame),
                     "autoreload": preferences.autoreload, "reload_seconds": preferences.cache_seconds},
            headers={"Cache-Control": "no-store"},
        )

    def get_preferences(session):
        preferences = session.get(UiSettings, 1)
        if preferences is None:
            raise HTTPException(500, "Einstellungen fehlen. Bitte Alembic-Migrationen ausführen.")
        return preferences

    @app.get("/profiles")
    def profiles_page(request: Request):
        with sessions() as session:
            profiles = session.scalars(
                select(BirthProfile).options(selectinload(BirthProfile.activations))
                .order_by(BirthProfile.name, BirthProfile.id)
            ).all()
            current_id = get_preferences(session).active_profile_id
            activations = {}
            topology = None
            for profile in profiles:
                key = activation_key(profile.birth_utc, settings.api_url, settings.engine_version)
                if is_fresh(profile, key):
                    lists = activation_lists(profile)
                    if topology is None:
                        try:
                            topology = load_topology(session)
                        except TopologyMissing as exc:
                            raise HTTPException(500, str(exc)) from exc
                    activations[profile.id] = {**lists, **derive(lists, topology)}
        current = next((profile for profile in profiles if profile.id == current_id), None)
        return templates.TemplateResponse(
            request=request, name="index.html",
            context={"profiles": profiles, "csrf": token(request), "current": current,
                     "activations": activations, "source_labels": SOURCE_LABELS,
                     "body_labels": BODY_LABELS},
        )

    @app.post("/profiles/current/clear")
    async def clear_current(request: Request):
        form = await request.form()
        check_csrf(request, form)
        with sessions.begin() as session:
            get_preferences(session).active_profile_id = None
        return RedirectResponse("/profiles", status_code=303)

    @app.post("/profiles/{profile_id}/current")
    async def select_current(request: Request, profile_id: str):
        form = await request.form()
        check_csrf(request, form)
        with sessions.begin() as session:
            get_profile(session, profile_id)
            get_preferences(session).active_profile_id = profile_id
        return RedirectResponse(f"/profiles#profile-{profile_id}", status_code=303)

    @app.get("/profiles/current/overlay")
    def current_overlay():
        # Sync route: FastAPI runs the potentially blocking API call in a worker thread.
        headers = {"Cache-Control": "no-store"}
        with sessions() as session:
            profile_id = get_preferences(session).active_profile_id
        if profile_id is None:
            return JSONResponse({"profile": None, "detail": "Kein aktuelles Geburtsprofil ausgewählt."},
                                headers=headers)
        try:
            return JSONResponse(natal.activations(profile_id), headers=headers)
        except ProfileChanged as exc:
            return JSONResponse({"detail": f"{exc} Bitte erneut versuchen."}, status_code=409,
                                headers=headers)
        except ChartServiceError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=502, headers=headers)
        except TopologyMissing as exc:
            return JSONResponse({"detail": str(exc)}, status_code=500, headers=headers)

    @app.get("/profiles/new")
    def new(request: Request):
        return form_page(request)

    @app.get("/transit")
    def transit(request: Request):
        with sessions() as session:
            preferences = session.get(UiSettings, 1)
            if preferences is None:
                raise HTTPException(500, "Einstellungen fehlen. Bitte Alembic-Migrationen ausführen.")
        try:
            frame, wheel = transit_cache.get(preferences.cache_seconds)
        except ChartServiceError as exc:
            raise HTTPException(502, str(exc)) from exc
        catalog = descriptions()
        return templates.TemplateResponse(
            request=request, name="transit.html",
            context={"frame": frame, "wheel": wheel, "autoreload": preferences.autoreload,
                     "gate_tooltip": lambda gate: gate_tooltip(gate, catalog),
                     "reload_seconds": preferences.cache_seconds},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/transit/next-gates")
    def next_gates():
        with sessions() as session:
            preferences = session.get(UiSettings, 1)
            if preferences is None:
                raise HTTPException(500, "Einstellungen fehlen. Bitte Alembic-Migrationen ausführen.")
        try:
            frame, _ = transit_cache.get(preferences.cache_seconds)
        except ChartServiceError as exc:
            raise HTTPException(502, str(exc)) from exc
        from fastapi.responses import JSONResponse
        return JSONResponse(forecasts.snapshot(frame), headers={"Cache-Control": "no-store"})

    def settings_page(request, values, errors=None, status=200):
        return templates.TemplateResponse(
            request=request, name="settings.html",
            context={"values": values, "options": CACHE_OPTIONS, "csrf": token(request),
                     "errors": errors or []},
            status_code=status,
        )

    @app.get("/settings")
    def show_settings(request: Request):
        with sessions() as session:
            preferences = session.get(UiSettings, 1)
            if preferences is None:
                raise HTTPException(500, "Einstellungen fehlen. Bitte Alembic-Migrationen ausführen.")
            values = {"cache_seconds": preferences.cache_seconds, "autoreload": preferences.autoreload}
        return settings_page(request, values)

    @app.post("/settings")
    async def save_settings(request: Request):
        form = await request.form()
        check_csrf(request, form)
        raw_seconds = str(form.get("cache_seconds", ""))
        values = {
            "cache_seconds": int(raw_seconds) if raw_seconds in {str(v) for v, _ in CACHE_OPTIONS} else 0,
            "autoreload": form.get("autoreload", ""),
        }
        try:
            data = SettingsInput.model_validate(values)
        except ValidationError:
            return settings_page(request, values, ["Bitte gültige Einstellungen auswählen."], 422)
        with sessions.begin() as session:
            preferences = session.get(UiSettings, 1)
            if preferences is None:
                raise HTTPException(500, "Einstellungen fehlen. Bitte Alembic-Migrationen ausführen.")
            preferences.cache_seconds = data.cache_seconds
            preferences.autoreload = data.autoreload
        return RedirectResponse("/settings", status_code=303)

    @app.get("/profiles/{profile_id}/edit")
    def edit(request: Request, profile_id: str):
        with sessions() as session:
            profile = get_profile(session, profile_id)
            values = {key: getattr(profile, key) for key in (
                "name", "birth_local", "timezone", "place"
            )}
            values["birth_local"] = profile.birth_local.isoformat(timespec="seconds")
        return form_page(request, values, profile_id)

    async def save(request, profile_id=None):
        form = await request.form()
        check_csrf(request, form)
        values = {key: form.get(key, "") for key in ("name", "birth_local", "timezone", "place")}
        try:
            data = ProfileInput.model_validate(values)
        except ValidationError as exc:
            errors = [f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in exc.errors()]
            return form_page(request, values, profile_id, errors, 422)
        utc = data.utc_time().replace(tzinfo=None)
        key = chart_key(utc, settings.api_url, settings.engine_version)
        natal_key = activation_key(utc, settings.api_url, settings.engine_version)
        with sessions.begin() as session:
            if profile_id:
                profile = get_profile(session, profile_id)
                if profile.chart_key != key:
                    profile.chart_svg = None
                    profile.chart_generated_at = None
                if profile.birth_utc != utc or profile.activation_key not in (None, natal_key):
                    profile.activations.clear()
                    profile.activation_key = None
                    profile.activation_engine_version = None
                    profile.activations_calculated_at = None
            else:
                profile = BirthProfile(id=str(uuid4()))
                session.add(profile)
            for field in ("name", "birth_local", "timezone", "place"):
                setattr(profile, field, getattr(data, field))
            profile.birth_utc = utc
            profile.chart_key = key
        return RedirectResponse("/profiles", status_code=303)

    @app.post("/profiles")
    async def create(request: Request):
        return await save(request)

    @app.post("/profiles/{profile_id}")
    async def modify(request: Request, profile_id: str):
        return await save(request, profile_id)

    @app.post("/profiles/{profile_id}/delete")
    async def delete(request: Request, profile_id: str):
        form = await request.form()
        check_csrf(request, form)
        with sessions.begin() as session:
            profile = get_profile(session, profile_id)
            preferences = get_preferences(session)
            if preferences.active_profile_id == profile_id:
                preferences.active_profile_id = None
                session.flush()
            session.delete(profile)
        return RedirectResponse("/profiles", status_code=303)

    @app.post("/profiles/{profile_id}/chart")
    async def generate(request: Request, profile_id: str):
        form = await request.form()
        check_csrf(request, form)
        # Blocking HTTP and database work belongs in a worker, not the event loop.
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(generate_chart, profile_id, form.get("refresh") == "1")

    def generate_chart(profile_id, refresh):
        with sessions() as session:
            profile = get_profile(session, profile_id)
        old_key = profile.chart_key
        key = chart_key(profile.birth_utc, settings.api_url, settings.engine_version)
        if refresh or not profile.chart_svg or old_key != key:
            try:
                svg = client.render(profile)
            except ChartServiceError as exc:
                raise HTTPException(502, str(exc)) from exc
            with sessions.begin() as session:
                result = session.execute(
                    update(BirthProfile)
                    .where(BirthProfile.id == profile_id, BirthProfile.chart_key == old_key)
                    .values(chart_svg=svg, chart_key=key,
                            chart_generated_at=datetime.now(UTC).replace(tzinfo=None))
                )
                if result.rowcount != 1:
                    raise HTTPException(409, "Profil wurde geändert. Bitte erneut berechnen.")
        return RedirectResponse(f"/profiles/{profile_id}/chart.svg", status_code=303)

    @app.get("/profiles/{profile_id}/chart.svg")
    def svg(profile_id: str):
        with sessions() as session:
            profile = get_profile(session, profile_id)
        key = chart_key(profile.birth_utc, settings.api_url, settings.engine_version)
        if not profile.chart_svg or profile.chart_key != key:
            raise HTTPException(404, "Kein aktuelles Chart gespeichert. Bitte berechnen.")
        return Response(
            enlarge_gate_labels(profile.chart_svg), media_type="image/svg+xml",
            headers={
                "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    @app.exception_handler(HTTPException)
    async def error_page(request: Request, exc: HTTPException):
        return templates.TemplateResponse(
            request=request, name="error.html",
            context={"message": exc.detail}, status_code=exc.status_code,
        )

    return app


app = create_app()
