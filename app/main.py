import secrets
import os
from urllib.parse import urlsplit
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
from sqlalchemy import select, update, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload
from starlette.middleware.sessions import SessionMiddleware

from app.api_client import ChartClient, ChartServiceError
from app.config import Settings
from app.database import make_database
from app.gate_forecast import GateForecastService
from app.experiments import SampleCache, experiment_router
from app.center_timeline import center_timeline_router
from app.gate_descriptions import CatalogMissing, gate_descriptions, gate_tooltip
from app.hd_catalog import CATEGORY_LABELS
from app.knowledge import entity_order, knowledge_url, navigation, validate_mapping
from app.knowledge_catalog import effective_locale, localized_record, resolve_texts
from app.models import BirthProfile, Channel, Circuit, Gate, HdEntity, UiSettings
from app.natal import (
    BODY_LABELS, CENTER_LABELS, SOURCE_LABELS, ChartUnavailable, NatalDataError, NatalService,
    ProfileChanged, TopologyMissing, activation_lists, derive,
    is_fresh, load_topology,
)
from app.profiles import ProfileInput, activation_key, chart_key
from app.profile_svg import enlarge_gate_labels
from app.transit_service import TransitCache


ROOT = Path(__file__).parent
CACHE_OPTIONS = ((60, "1m"), (300, "5m"), (600, "10m"), (1800, "30m"), (3600, "1h"))
TIMELINE_SAMPLE_OPTIONS = (
    (32, "Schnell · bis 32 Punkte"),
    (64, "Standard · bis 64 Punkte"),
    (160, "Fein · bis 160 Punkte"),
)


class SettingsInput(BaseModel):
    cache_seconds: Literal[60, 300, 600, 1800, 3600]
    autoreload: bool
    center_timeline_samples: Literal[32, 64, 160] = 64
    catalog_locale: Literal["", "de", "en"] = ""


def create_app(settings: Settings | None = None, chart_client=None, *, accounts_config=None, mailer=None) -> FastAPI:
    settings = settings or Settings()
    engine, sessions = make_database(settings.database_url)
    templates = Jinja2Templates(directory=ROOT / "templates")
    validate_mapping()
    templates.env.globals["knowledge_url"] = knowledge_url
    multi_user = accounts_config.enabled if accounts_config is not None else os.getenv("HDGUI_MULTI_USER", "").lower() in ("1", "true", "yes")
    accounts = None
    if multi_user:
        from app import accounts as account_api
        from app.accounts import AccountsConfig, AccountService
        accounts = AccountService(sessions, accounts_config or AccountsConfig.from_env(), mailer=mailer)
        api_url = urlsplit(settings.api_url)
        if (api_url.scheme != "https" or not api_url.hostname or api_url.username
                or api_url.password or api_url.query or api_url.fragment):
            raise ValueError("Mehrbenutzerbetrieb benötigt eine fest konfigurierte HTTPS-Berechnungs-API.")
        if chart_client is None and not settings.api_token:
            raise ValueError("Mehrbenutzerbetrieb benötigt HD_API_TOKEN für die externe Berechnungs-API.")

    def actor(request):
        if not accounts:
            return None
        current = accounts.authenticate(request.cookies.get(accounts.config.session_cookie_name))
        if current is None:
            raise HTTPException(401, "Anmeldung erforderlich.")
        return current

    def get_preferences(session, request=None):
        if accounts:
            return account_api.preferences(session, actor(request))
        preferences = session.get(UiSettings, 1)
        if preferences is None:
            raise HTTPException(500, "Einstellungen fehlen. Bitte Alembic-Migrationen ausführen.")
        return preferences

    def request_locale(session, request=None):
        if accounts and request is not None:
            from app.knowledge_catalog import negotiate_locale
            return get_preferences(session, request).catalog_locale or negotiate_locale(request.headers.get("accept-language"))
        return effective_locale(session, request.headers.get("accept-language") if request else None)

    def descriptions(request=None):
        with sessions() as session:
            try:
                return gate_descriptions(session, request_locale(session, request))
            except CatalogMissing as exc:
                raise HTTPException(500, str(exc)) from exc
            except SQLAlchemyError as exc:
                raise HTTPException(
                    500, "Katalog oder Einstellungen fehlen. Bitte Alembic-Migrationen ausführen."
                ) from exc

    client = chart_client or ChartClient(settings)
    samples = SampleCache(client, sessions, settings)
    transit_cache = TransitCache(client, descriptions)
    forecasts = GateForecastService(samples)
    natal = NatalService(sessions, client, settings, require_authorization=multi_user)

    @asynccontextmanager
    async def lifespan(app):
        yield
        forecasts.close()
        engine.dispose()

    app = FastAPI(title="HDGUI", lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="strict")
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    app.state.account_service = accounts
    if accounts:
        app.include_router(account_api.create_auth_router(accounts))
        from app.sharing import sharing_router
        app.include_router(sharing_router(accounts, templates, actor, token=lambda request: token(request),
                                          check_csrf=lambda request, form: check_csrf(request, form)))

        @app.middleware("http")
        async def private_application(request: Request, call_next):
            from starlette.concurrency import run_in_threadpool
            path = request.url.path
            if request.method == "POST":
                body = await request.body()
                if len(body) > 65536:
                    return JSONResponse({"detail": "Anfrage zu groß."}, status_code=413)
            public = path in ("/auth/login", "/auth/confirm") or path.startswith("/static/")
            if not public:
                current = await run_in_threadpool(
                    accounts.authenticate, request.cookies.get(accounts.config.session_cookie_name),
                )
                if current is None:
                    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                        return RedirectResponse("/auth/login", status_code=303)
                    return JSONResponse({"detail": "Anmeldung erforderlich."}, status_code=401)
                request.state.actor = current
                if not path.startswith("/auth/"):
                    from sqlalchemy import func
                    with sessions() as session:
                        unowned = session.scalar(select(func.count()).select_from(BirthProfile).where(BirthProfile.owner_user_id.is_(None)))
                    if unowned:
                        return JSONResponse({"detail": "Bestehende Profile zuerst ausdrücklich einem verifizierten Erstbesitzer zuordnen."}, status_code=503)
                expensive = path in ("/", "/transit", "/transit/next-gates", "/experiments/frame", "/experiments/data", "/centers/frame", "/profiles/current/overlay")
                expensive = expensive or (request.method == "POST" and path.endswith(("/chart", "/analysis")))
                if expensive:
                    cost = 1
                    if path == "/experiments/data":
                        days = request.query_params.get("days", "1")
                        cost = {"1": 25, "7": 29, "30": 31}.get(days, 31)
                    allowed = await run_in_threadpool(
                        accounts.check_rate_limit, current, "calculations", 300, 3600,
                        cost=cost, global_limit=3000,
                    )
                    if not allowed:
                        return JSONResponse({"detail": "Berechnungsbudget erschöpft. Bitte später erneut versuchen."},
                                            status_code=429, headers={"Retry-After": "3600"})
            request.state.csrf = request.cookies.get("hdgui_csrf") or secrets.token_urlsafe(32)
            request.state.read_profiles = set()
            response = await call_next(request)
            if not public and not path.startswith("/auth/") and request.method == "GET":
                latest = await run_in_threadpool(
                    accounts.authenticate, request.cookies.get(accounts.config.session_cookie_name),
                )
                if latest is None:
                    return JSONResponse({"detail": "Anmeldung nicht mehr gültig."}, status_code=401)
                try:
                    with sessions() as session:
                        for key in request.state.read_profiles:
                            account_api.readable_profile(session, latest, key)
                except HTTPException as exc:
                    return JSONResponse({"detail": "Profil nicht mehr verfügbar."}, status_code=exc.status_code)
            if not request.cookies.get("hdgui_csrf") and not path.startswith("/auth/"):
                response.set_cookie("hdgui_csrf", request.state.csrf, secure=accounts.config.secure_cookie,
                                    httponly=False, samesite="lax", max_age=86400)
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

        from app.http_limits import RequestSizeLimit
        app.add_middleware(RequestSizeLimit, max_bytes=65536)

    def current_profile(request=None):
        # Database only: selecting or showing the current profile never calls the API.
        try:
            with sessions() as session:
                preferences = get_preferences(session, request)
                if preferences is None or preferences.active_profile_id is None:
                    return None
                profile = get_profile(session, preferences.active_profile_id, request)
                return None if profile is None else {"id": profile.id, "name": profile.name}
        except SQLAlchemyError:
            # The overlay is optional; the transit timeline stays usable without profiles.
            return {"error": "Profil-Datenbank nicht verfügbar. Bitte Alembic-Migrationen ausführen."}

    app.include_router(experiment_router(client, templates, current_profile, descriptions, samples))
    app.include_router(center_timeline_router(
        templates, current_profile, descriptions, natal, sessions, samples,
        preferences=get_preferences,
        authorize=(lambda session, profile_id, request: get_profile(session, profile_id, request)) if accounts else None,
        natal_options=lambda request, profile_id: natal_access(request, profile_id),
    ))

    @app.get("/knowledge")
    def knowledge_page(
        request: Request, area: str | None = None, category: str | None = None,
        entity: str | None = None, gate: int | None = None,
    ):
        context = navigation(area, category)
        category = context["category"]
        if gate is not None and (category != "gate-line" or not 1 <= gate <= 64):
            raise HTTPException(404, "Ungültige Tor-Auswahl.")
        if entity is not None and request.query_params.get("category") is None:
            raise HTTPException(404, "Eintrag benötigt eine Kategorie.")
        context.update(workspace=True, selected_entity=entity, selected_gate=gate)
        if category in ("gate", "channel", "circuit"):
            context.update(catalog_context(request))
            entries = {
                "gate": [(f"gate.{g.number}", f"Tor {g.number} · {g.name}") for g in context["gates"]],
                "channel": [(f"channel.{c.gate_a}-{c.gate_b}", f"Kanal {c.gate_a}–{c.gate_b}") for c in context["channels"]],
                "circuit": [(f"circuit.{c.id}", c.name) for c in context["circuits"]],
            }[category]
            if entity is not None and entity not in dict(entries):
                raise HTTPException(404, "Eintrag gehört nicht zu dieser Kategorie.")
            context["entry_links"] = entries
            context["current_profile"] = current_profile(request) if category == "gate" else None
            template = "gates.html" if category == "gate" else "channels.html"
        else:
            try:
                with sessions() as session:
                    records = sorted(session.scalars(
                        select(HdEntity).where(HdEntity.category == category),
                    ).all(), key=entity_order)
                    locale = request_locale(session, request)
                    texts = resolve_texts(session, [row.id for row in records], locale)
                    entities = [localized_record(row, texts.get(row.id)) for row in records]
                    context["catalog_locale"] = locale
            except SQLAlchemyError as exc:
                raise HTTPException(500, "Entitätenkatalog nicht verfügbar. Bitte Alembic-Migrationen ausführen.") from exc
            selected = next((row for row in entities if row.id == entity), None)
            if entity is not None and selected is None:
                raise HTTPException(404, "Eintrag gehört nicht zu dieser Kategorie.")
            if category == "gate-line":
                if selected is not None:
                    entity_gate = selected.structure["gate"]
                    if gate is not None and gate != entity_gate:
                        raise HTTPException(404, "Tor und Torlinie widersprechen sich.")
                    gate = entity_gate
                context["selected_gate"] = gate
                entities = [row for row in entities if row.structure["gate"] == gate]
            context.update(
                entities=[selected] if selected else [],
                entry_links=[(row.id, row.name) for row in entities],
                empty_category=not entities and category != "gate-line",
            )
            if category == "center" and selected is not None:
                context["center_gates"] = [
                    row for row in catalog_context(request)["gates"] if row.center == selected.id.split(".", 1)[1]
                ]
            template = "hd_entities.html"
        return templates.TemplateResponse(
            request=request, name=template, context=context,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/knowledge/help")
    def knowledge_help(request: Request):
        return templates.TemplateResponse(request=request, name="knowledge.html", context={})

    @app.get("/knowledge/celestial-bodies")
    def celestial_bodies_page(request: Request):
        body_ids = (
            "body.Sun", "body.Earth", "body.Moon", "body.Mercury", "body.Venus",
            "body.Mars", "body.Jupiter", "body.Saturn", "body.Uranus", "body.Neptune",
            "body.Pluto", "body.North_Node", "body.South_Node",
        )
        try:
            with sessions() as session:
                locale = request_locale(session, request)
                texts = resolve_texts(session, body_ids, locale, "astrology-symbolism")
                bodies = [
                    {
                        "id": entity_id,
                        "name": resolved.record.name if resolved else entity_id,
                        "description": resolved.record.description if resolved else None,
                        "metadata": resolved.record.metadata_json if resolved else {},
                        "locale": resolved.record.locale if resolved else None,
                        "is_fallback": resolved.is_fallback if resolved else True,
                        "review_status": resolved.record.review_status if resolved else "unknown",
                    }
                    for entity_id in body_ids
                    for resolved in [texts.get(entity_id)]
                ]
        except SQLAlchemyError as exc:
            raise HTTPException(
                500, "Wissenskatalog nicht verfügbar. Bitte Alembic-Migrationen ausführen."
            ) from exc
        return templates.TemplateResponse(
            request=request, name="celestial_bodies.html",
            context={"bodies": bodies, "catalog_locale": locale, **navigation(category="body")},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/knowledge/entities")
    def entities_page(request: Request, category: str = "type"):
        if category not in CATEGORY_LABELS:
            raise HTTPException(404, "Unbekannte Entitäten-Kategorie.")
        try:
            with sessions() as session:
                records = sorted(session.scalars(
                    select(HdEntity).where(HdEntity.category == category),
                ).all(), key=entity_order)
                locale = request_locale(session, request)
                texts = resolve_texts(session, [entity.id for entity in records], locale)
                entities = [
                    localized_record(entity, texts.get(entity.id))
                    for entity in records
                ]
        except SQLAlchemyError as exc:
            raise HTTPException(500, "Entitätenkatalog nicht verfügbar. Bitte Alembic-Migrationen ausführen.") from exc
        return templates.TemplateResponse(
            request=request, name="hd_entities.html",
            context={
                "entities": entities, "catalog_locale": locale, **navigation(category=category),
            },
            headers={"Cache-Control": "no-store"},
        )

    def catalog_context(request):
        with sessions() as session:
            try:
                load_topology(session)
                circuits = session.scalars(select(Circuit).order_by(Circuit.id)).all()
                gates = session.scalars(select(Gate).order_by(Gate.number)).all()
                channels = session.scalars(select(Channel).order_by(Channel.gate_a, Channel.gate_b)).all()
                locale = request_locale(session, request)
                ids = (
                    [f"circuit.{circuit.id}" for circuit in circuits]
                    + [f"gate.{gate.number}" for gate in gates]
                    + [f"channel.{channel.gate_a}-{channel.gate_b}" for channel in channels]
                )
                texts = resolve_texts(session, ids, locale)
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
            gates = [
                localized_record(gate, texts.get(f"gate.{gate.number}"))
                for gate in gates
            ]
            channels = [
                localized_record(
                    channel, texts.get(f"channel.{channel.gate_a}-{channel.gate_b}"),
                )
                for channel in channels
            ]
            circuits = [
                localized_record(circuit, texts.get(f"circuit.{circuit.id}"))
                for circuit in circuits
            ]
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
                "circuit_members": circuit_members, "catalog_locale": locale}

    @app.get("/gates")
    def gates_page(request: Request):
        return templates.TemplateResponse(
            request=request, name="gates.html",
            context={**catalog_context(request), "current_profile": current_profile(request), **navigation(category="gate")},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/channels")
    def channels_page(request: Request):
        return templates.TemplateResponse(
            request=request, name="channels.html", context={**catalog_context(request), **navigation(category="channel")},
        )

    def token(request):
        if accounts:
            return request.state.csrf
        if "csrf" not in request.session:
            request.session["csrf"] = secrets.token_urlsafe(32)
        return request.session["csrf"]

    def check_csrf(request, form):
        if accounts:
            if not account_api.csrf_valid(request, str(form.get("csrf", ""))):
                raise HTTPException(403, "Ungültige Formularsitzung. Bitte die Seite neu laden.")
            return
        supplied = str(form.get("csrf", ""))
        expected = request.session.get("csrf")
        if not expected or not secrets.compare_digest(supplied, expected):
            raise HTTPException(403, "Ungültige Formularsitzung. Bitte die Seite neu laden.")

    def get_profile(session, profile_id, request=None, *, write=False):
        if accounts:
            if not write:
                request.state.read_profiles.add(profile_id)
            return account_api.owned_profile(session, actor(request), profile_id) if write else account_api.readable_profile(session, actor(request), profile_id)
        profile = session.get(BirthProfile, profile_id)
        if profile is None:
            raise HTTPException(404, "Profil nicht gefunden.")
        return profile

    def natal_access(request, profile_id, *, write=False):
        if not accounts:
            return {}
        with sessions() as session:
            profile = get_profile(session, profile_id, request, write=write)
            owns = profile.owner_user_id == actor(request).id
        return {
            "authorize": lambda session, key: get_profile(session, key, request, write=write or owns),
            "allow_compute": owns,
        }

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
            preferences = get_preferences(session, request)
            if preferences is None:
                raise HTTPException(500, "Einstellungen fehlen. Bitte Alembic-Migrationen ausführen.")
        try:
            catalog = descriptions(request)
            frame, _ = transit_cache.get(preferences.cache_seconds, catalog)
        except ChartServiceError as exc:
            raise HTTPException(502, str(exc)) from exc
        return templates.TemplateResponse(
            request=request, name="home.html",
            context={"frame": frame, "bodygraph": transit_cache.bodygraph(frame, catalog),
                     "autoreload": preferences.autoreload, "reload_seconds": preferences.cache_seconds},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/profiles")
    def profiles_page(request: Request, page: int = 1, q: str = "", scope: str = "owned"):
        if page < 1 or len(q) > 120 or scope not in ("owned", "shared"):
            raise HTTPException(422, "Ungültige Profilsuche.")
        with sessions() as session:
            query = select(BirthProfile).options(
                    selectinload(BirthProfile.activations), selectinload(BirthProfile.birth_chart),
                ).order_by(BirthProfile.name, BirthProfile.id)
            if accounts:
                current_actor = actor(request)
                query = query.where(account_api.profile_scope(current_actor, shared=scope == "shared"))
                if scope == "shared":
                    query = query.where(BirthProfile.owner_user_id != current_actor.id)
                if q:
                    query = query.where(BirthProfile.name.contains(q, autoescape=True))
                query = query.offset((page - 1) * 25).limit(26)
            profiles = session.scalars(query).all()
            has_next = accounts is not None and len(profiles) > 25
            if accounts:
                profiles = profiles[:25]
                request.state.read_profiles.update(profile.id for profile in profiles)
            current_id = get_preferences(session, request).active_profile_id
            activations = {}
            chart_status = {}
            topology = None
            for profile in profiles:
                key = activation_key(profile.birth_utc, settings.api_url, settings.engine_version)
                chart_status[profile.id] = (
                    "current" if profile.birth_chart and profile.birth_chart.calculation_key == key
                    else "stale" if profile.birth_chart else "missing"
                )
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
                     "multi_user": multi_user, "actor_id": actor(request).id if accounts else None,
                     "scope": scope, "page": page, "q": q, "has_next": has_next,
                     "activations": activations, "source_labels": SOURCE_LABELS,
                     "body_labels": BODY_LABELS, "chart_status": chart_status},
        )

    @app.post("/profiles/current/clear")
    async def clear_current(request: Request):
        form = await request.form()
        check_csrf(request, form)
        with sessions.begin() as session:
            get_preferences(session, request).active_profile_id = None
        return RedirectResponse("/profiles", status_code=303)

    @app.post("/profiles/{profile_id}/current")
    async def select_current(request: Request, profile_id: str):
        form = await request.form()
        check_csrf(request, form)
        with sessions.begin() as session:
            get_profile(session, profile_id, request)
            get_preferences(session, request).active_profile_id = profile_id
        return RedirectResponse(f"/profiles#profile-{profile_id}", status_code=303)

    @app.get("/profiles/current/overlay")
    def current_overlay(request: Request):
        # Sync route: FastAPI runs the potentially blocking API call in a worker thread.
        headers = {"Cache-Control": "no-store"}
        with sessions() as session:
            profile_id = get_preferences(session, request).active_profile_id
        if profile_id is None:
            return JSONResponse({"profile": None, "detail": "Kein aktuelles Geburtsprofil ausgewählt."},
                                headers=headers)
        try:
            return JSONResponse(natal.activations(profile_id, **natal_access(request, profile_id)), headers=headers)
        except ChartUnavailable as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409, headers=headers)
        except ProfileChanged as exc:
            return JSONResponse({"detail": f"{exc} Bitte erneut versuchen."}, status_code=409,
                                headers=headers)
        except (ChartServiceError, NatalDataError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=502, headers=headers)
        except TopologyMissing as exc:
            return JSONResponse({"detail": str(exc)}, status_code=500, headers=headers)
        except SQLAlchemyError:
            return JSONResponse(
                {"detail": "Geburtschart-Datenbank nicht verfügbar. Bitte Alembic-Migrationen prüfen."},
                status_code=500, headers=headers,
            )

    @app.get("/profiles/{profile_id}/analysis")
    def stored_analysis(request: Request, profile_id: str):
        headers = {"Cache-Control": "no-store"}
        try:
            options = natal_access(request, profile_id)
            return JSONResponse(natal.stored_chart(profile_id, authorize=options.get("authorize")), headers=headers)
        except ChartUnavailable as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404, headers=headers)
        except ProfileChanged as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409, headers=headers)
        except (TopologyMissing, SQLAlchemyError):
            return JSONResponse(
                {"detail": "Geburtschart-Datenbank nicht verfügbar. Bitte Alembic-Migrationen prüfen."},
                status_code=500, headers=headers,
            )

    @app.post("/profiles/{profile_id}/analysis")
    async def calculate_analysis(request: Request, profile_id: str):
        form = await request.form()
        check_csrf(request, form)
        from starlette.concurrency import run_in_threadpool
        try:
            with sessions() as session:
                get_profile(session, profile_id, request, write=True)
            await run_in_threadpool(
                natal.activations, profile_id, require_details=True, refresh=form.get("refresh") == "1",
                **natal_access(request, profile_id, write=True),
            )
        except ProfileChanged as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ChartServiceError, NatalDataError) as exc:
            raise HTTPException(502, str(exc)) from exc
        except (TopologyMissing, SQLAlchemyError) as exc:
            raise HTTPException(500, "Geburtschart-Datenbank nicht verfügbar. Bitte Migrationen prüfen.") from exc
        return RedirectResponse(f"/profiles/{profile_id}/analysis", status_code=303)

    @app.get("/profiles/new")
    def new(request: Request):
        return form_page(request)

    @app.get("/transit")
    def transit(request: Request):
        with sessions() as session:
            preferences = get_preferences(session, request)
            if preferences is None:
                raise HTTPException(500, "Einstellungen fehlen. Bitte Alembic-Migrationen ausführen.")
        try:
            catalog = descriptions(request)
            frame, wheel = transit_cache.get(preferences.cache_seconds, catalog)
        except ChartServiceError as exc:
            raise HTTPException(502, str(exc)) from exc
        return templates.TemplateResponse(
            request=request, name="transit.html",
            context={"frame": frame, "wheel": wheel, "autoreload": preferences.autoreload,
                     "gate_tooltip": lambda gate: gate_tooltip(gate, catalog),
                     "reload_seconds": preferences.cache_seconds},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/transit/next-gates")
    def next_gates(request: Request):
        with sessions() as session:
            preferences = get_preferences(session, request)
            if preferences is None:
                raise HTTPException(500, "Einstellungen fehlen. Bitte Alembic-Migrationen ausführen.")
        try:
            frame, _ = transit_cache.get(preferences.cache_seconds, descriptions(request))
        except ChartServiceError as exc:
            raise HTTPException(502, str(exc)) from exc
        from fastapi.responses import JSONResponse
        return JSONResponse(forecasts.snapshot(frame), headers={"Cache-Control": "no-store"})

    def settings_page(request, values, errors=None, status=200):
        return templates.TemplateResponse(
            request=request, name="settings.html",
            context={"values": values, "options": CACHE_OPTIONS,
                     "timeline_sample_options": TIMELINE_SAMPLE_OPTIONS, "csrf": token(request),
                     "errors": errors or []},
            status_code=status,
        )

    @app.get("/settings")
    def show_settings(request: Request):
        with sessions() as session:
            preferences = get_preferences(session, request)
            if preferences is None:
                raise HTTPException(500, "Einstellungen fehlen. Bitte Alembic-Migrationen ausführen.")
            values = {
                "cache_seconds": preferences.cache_seconds,
                "autoreload": preferences.autoreload,
                "center_timeline_samples": preferences.center_timeline_samples,
                "catalog_locale": preferences.catalog_locale or "",
            }
        return settings_page(request, values)

    @app.get("/account")
    def account_page(request: Request):
        if not accounts:
            raise HTTPException(404, "Kontofunktionen sind nicht aktiviert.")
        return templates.TemplateResponse(request=request, name="account.html", context={
            "email": actor(request).email, "csrf": token(request),
        })

    @app.post("/settings")
    async def save_settings(request: Request):
        form = await request.form()
        check_csrf(request, form)
        raw_seconds = str(form.get("cache_seconds", ""))
        with sessions() as session:
            preferences = get_preferences(session, request)
            if preferences is None:
                raise HTTPException(500, "Einstellungen fehlen. Bitte Alembic-Migrationen ausführen.")
            current_samples = preferences.center_timeline_samples
        raw_samples = str(form.get("center_timeline_samples", current_samples))
        values = {
            "cache_seconds": int(raw_seconds) if raw_seconds in {str(v) for v, _ in CACHE_OPTIONS} else 0,
            "autoreload": form.get("autoreload", "") in ("on", "true", "1"),
            "center_timeline_samples": int(raw_samples)
            if raw_samples in {str(v) for v, _ in TIMELINE_SAMPLE_OPTIONS} else 0,
            "catalog_locale": str(form.get("catalog_locale", "")),
        }
        try:
            data = SettingsInput.model_validate(values)
        except ValidationError:
            return settings_page(request, values, ["Bitte gültige Einstellungen auswählen."], 422)
        with sessions.begin() as session:
            preferences = get_preferences(session, request)
            if preferences is None:
                raise HTTPException(500, "Einstellungen fehlen. Bitte Alembic-Migrationen ausführen.")
            preferences.cache_seconds = data.cache_seconds
            preferences.autoreload = data.autoreload
            preferences.center_timeline_samples = data.center_timeline_samples
            preferences.catalog_locale = data.catalog_locale or None
        return RedirectResponse("/settings", status_code=303)

    @app.get("/profiles/{profile_id}/edit")
    def edit(request: Request, profile_id: str):
        with sessions() as session:
            profile = get_profile(session, profile_id, request, write=True)
            values = {key: getattr(profile, key) for key in (
                "name", "birth_local", "timezone", "place"
            )}
            values["birth_local"] = profile.birth_local.isoformat(timespec="seconds")
        return form_page(request, values, profile_id)

    async def save(request, profile_id=None):
        form = await request.form()
        check_csrf(request, form)
        if profile_id is not None:
            with sessions() as session:
                get_profile(session, profile_id, request, write=True)
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
                profile = get_profile(session, profile_id, request, write=True)
                if profile.chart_key != key:
                    profile.chart_svg = None
                    profile.chart_generated_at = None
                if profile.birth_utc != utc or profile.activation_key not in (None, natal_key):
                    profile.activations.clear()
                    profile.activation_key = None
                    profile.activation_engine_version = None
                    profile.activations_calculated_at = None
                    profile.birth_chart = None
                elif profile.birth_chart is not None and profile.birth_chart.calculation_key != natal_key:
                    profile.birth_chart = None
            else:
                profile = BirthProfile(id=str(uuid4()), owner_user_id=actor(request).id if accounts else None)
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
            profile = get_profile(session, profile_id, request, write=True)
            preferences = get_preferences(session, request)
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
        return await run_in_threadpool(generate_chart, request, profile_id, form.get("refresh") == "1")

    def generate_chart(request, profile_id, refresh):
        with sessions() as session:
            profile = get_profile(session, profile_id, request, write=True)
        old_key = profile.chart_key
        key = chart_key(profile.birth_utc, settings.api_url, settings.engine_version)
        if refresh or not profile.chart_svg or old_key != key:
            try:
                svg = client.render(profile)
            except ChartServiceError as exc:
                raise HTTPException(502, str(exc)) from exc
            with sessions.begin() as session:
                get_profile(session, profile_id, request, write=True)
                result = session.execute(
                    update(BirthProfile)
                    .where(BirthProfile.id == profile_id, BirthProfile.chart_key == old_key)
                    .values(chart_svg=svg, chart_key=key,
                            chart_generated_at=datetime.now(UTC).replace(tzinfo=None))
                )
                if result.rowcount != 1:
                    raise HTTPException(409, "Profil wurde geändert. Bitte erneut berechnen.")
        return RedirectResponse(f"/profiles/{profile_id}/chart", status_code=303)

    @app.get("/profiles/{profile_id}/chart")
    def chart_page(request: Request, profile_id: str):
        with sessions() as session:
            get_profile(session, profile_id, request)
            profile = session.get(
                BirthProfile, profile_id, options=[
                    selectinload(BirthProfile.activations), selectinload(BirthProfile.birth_chart),
                ],
            )
            if profile is None:
                raise HTTPException(404, "Profil nicht gefunden.")
        key = chart_key(profile.birth_utc, settings.api_url, settings.engine_version)
        if not profile.chart_svg or profile.chart_key != key:
            raise HTTPException(404, "Kein aktuelles Chart gespeichert. Bitte berechnen.")
        natal_key = activation_key(profile.birth_utc, settings.api_url, settings.engine_version)
        chart = profile.birth_chart
        chart_is_current = bool(
            chart and chart.calculation_key == natal_key and is_fresh(profile, natal_key)
        )
        properties = []
        variables = []
        centers = None
        calculated_at = None
        catalog_locale = "de"
        if chart_is_current:
            with sessions() as session:
                records = {
                    entity.id: entity
                    for entity in session.scalars(
                        select(HdEntity).where(HdEntity.id.in_(set(chart.catalog_links.values()))),
                    )
                }
                locale = request_locale(session, request)
                catalog_locale = locale
                texts = resolve_texts(session, set(records), locale)
                entities = {
                    entity_id: localized_record(entity, texts.get(entity_id))
                    for entity_id, entity in records.items()
                }
            fields = (
                ("Typ", "energy_type", "energy_type"),
                ("Strategie", "strategy", "strategy"),
                ("Innere Autorität", "inner_authority", "inner_authority"),
                ("Profil", "profile", "profile"),
                ("Definition", "definition", "definition"),
                ("Inkarnationskreuz", "inc_cross", "incarnation_cross"),
                ("Signatur", "signature", "signature"),
                ("Nicht-Selbst-Thema", "not_self", "not_self"),
                ("Aura", "aura", "aura"),
            )
            properties = [
                {
                    "label": label,
                    "value": entities[chart.catalog_links[link_key]].name
                    if chart.catalog_links.get(link_key) in entities
                    else chart.general.get(value_key, "Nicht angegeben"),
                    "entity": entities.get(chart.catalog_links.get(link_key)),
                }
                for label, value_key, link_key in fields
            ]
            variable_labels = {
                "top_left": "Oben links", "bottom_left": "Unten links",
                "bottom_right": "Unten rechts", "top_right": "Oben rechts",
            }
            variables = [
                {
                    "label": variable_labels[slot],
                    "value": value,
                    "direction": entities.get(chart.catalog_links.get(f"variables.{slot}.direction")),
                }
                for slot, value in chart.variables.items()
                if slot in variable_labels
            ]
            centers = chart.centers
            calculated_at = chart.calculated_at
        return templates.TemplateResponse(
            request=request, name="profile_chart.html",
            context={
                "profile": profile, "csrf": token(request), "properties": properties,
                "variables": variables, "centers": centers, "analysis_current": chart_is_current,
                "analysis_stale": chart is not None and not chart_is_current,
                "analysis_calculated_at": calculated_at,
                "catalog_locale": catalog_locale,
                "is_owner": not accounts or profile.owner_user_id == actor(request).id,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/profiles/{profile_id}/chart.svg")
    def svg(request: Request, profile_id: str):
        with sessions() as session:
            profile = get_profile(session, profile_id, request)
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
