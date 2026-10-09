# Architecture

[Documentation index](../README.md#documentation) · [Operations](operations.md) · [Practice](practice.en.md)

This document describes the implementation, not the earlier aspirational design. Recommendations below are explicitly **proposed**, not already available.

## Runtime boundaries

```text
Browser: HTML / SVG / local CSS and JavaScript
    |
FastAPI + Jinja2 (app/main.py, app/experiments.py)
    |-- SQLAlchemy sessions -> local database, managed by Alembic
    |-- TransitCache / GateForecastService / SampleCache -> process memory
    |-- NatalService -> validated activations + persistent database cache
    |
ChartClient (app/api_client.py) -> HTTP calculation service
    |-- /calculate -> planetary positions, gates, lines
    `-- /bodygraph -> profile SVG

python -m app / start.sh -> frontend address check + optional Docker backend startup
```

FastAPI renders pages and handles forms/JSON endpoints. Jinja2 provides server-rendered templates; local JavaScript adds timeline loading, profile overlays, catalog highlights and geometric animation. This is **not an HTMX application**. No external browser chart library/resource is required for the timeline.

The WebUI does not implement a second astronomical ephemeris or the engine's design-time calculation. `ChartClient` owns upstream transport/error handling and response mapping. Normalized Pydantic `TransitFrame`, `TransitPosition` and `NatalActivations` models validate calculation data before domain use. The client requests UTC at a zero-offset reference location; see [time handling](operations.md#storage-time-handling-and-global-settings).

## Modules and responsibilities

| Area | Current responsibility |
|---|---|
| `main.py` | App factory, profile/settings/catalog routes, CSRF forms, service wiring |
| `config.py`, `backend.py`, `__main__.py` | Environment settings and opt-out local Docker startup |
| `api_client.py` | `/calculate` and `/bodygraph` transport, parsing and error translation |
| `profiles.py`, `models.py`, `database.py` | Birth validation, cache keys, ORM models and session factory |
| `natal.py` | Source validation, lazy persistence, derived gates/channels/centers and stale-result protection |
| `transit.py`, `bodygraph.py`, `profile_svg.py` | Normalized positions, wheel/BodyGraph rendering and saved-profile SVG presentation |
| `transit_service.py`, `gate_forecast.py` | Current snapshot/render cache and background future gate-change search |
| `experiments.py`, `static/gate-timeline.js` | Sample planning/cache, progressive timeline data and navigation |
| `catalog_fixture.py`, `gate_descriptions.py` | Explicit complete-fixture validation/import and database-backed wording |

The app factory can accept a replacement client, which supports testing and a future provider boundary. Today this is a concrete client/adapter pattern, **not** a complete provider protocol, plugin registry or interchangeable engine framework.

## Domain and persistent data

Birth profiles store a UUID, label, local birth time, IANA timezone, UTC instant and place label. SVG storage and natal-activation storage are independent; neither is a substitute for the other. Normalized natal rows distinguish `personality` and `design`, body, longitude, gate and line.

`ui_settings` contains global transit refresh preferences and nullable `active_profile_id`. Selecting a profile does not call the engine. Deleting it clears selection and deletes its dependent activation data. SQLite foreign-key enforcement is enabled; each request/service operation opens its own SQLAlchemy session.

Canonical topology comprises **64 gates, 36 unordered channel pairs and seven circuits**, created by frozen migrations. Each gate has a center; each channel has exactly one circuit. Integration includes 10–20, 10–57, 20–34 and 34–57; centering includes 10–34 and 25–51. Catalog imports can replace names/descriptions, not these relationships.

Natal derivation reads database gate/channel topology and takes the union of personality/design active gates: two active endpoints complete a channel, and complete channels define their endpoint centers. The home BodyGraph instead depicts **current transit only**. A selected natal profile is not merged into that graph.

Database topology is not yet a single source for every renderer: frozen migrations and runtime rendering/domain constants retain overlapping gate/channel knowledge. A recommendation below addresses this duplication; do not describe the current code as a universally database-driven topology engine.

Catalog pages expose gates, harmonic partners, channels and circuit relations. Tooltip/render text comes from database records. Historical profile-specific CSV annotations are not universal domain data. Fresh migrations need no private fixture; optional imports follow the [operations reference](operations.md#catalog-imports-and-private-fixtures).

## API and presentation flows

| WebUI route | Behavior |
|---|---|
| `/`, `/transit` | Current-only BodyGraph and zodiac wheel/table from the shared snapshot |
| `/profiles` | Persistent profile management and saved charts; select one current profile |
| `/profiles/current/overlay` | Lazily obtain/cache natal activations and derived relationships |
| `/settings` | Global snapshot TTL and page autoreload |
| `/gates`, `/channels` | Linked catalog; optional current-profile highlighting on gates |
| `/experiments/gate-plot` | Sampled, zoomable gate timeline with optional fixed natal bands |
| `/experiments/timeline` | Validate window and return a sample plan, without calculation |
| `/experiments/frame` | Fetch/cache one actual timestamp, all returned bodies |
| `/experiments/ephemeris`, `/experiments/series` | Explicitly loaded UTC position/series tables |
| `/experiments/gate-walk` | API-independent geometric zodiac-order learning animation |

For transits, the adapter uses only `/calculate` → `gates.prs.Planets`; the engine's design positions are not current transits. Natal calculations use both `gates.prs.Planets` and `gates.des.Planets`, require nonempty valid lists with unique matching body sets, and never save invalid responses as empty activations.

Profile SVG is XML-parsed and served as an image or a document with restrictive Content Security Policy, not inserted as untrusted page HTML. CSRF checks protect mutation forms. These measures do not establish an authenticated security boundary.

Timeline natal overlays are fixed personality/design bands, with textual/tabular alternatives. They do not claim that natal positions move. One successful overlay load is reused within the page; disabled/stale responses are discarded. Profile selection in another tab takes effect after a page reload.

## Cache and concurrency model

| Cache/service | Lifetime and coordination |
|---|---|
| Current transit | In-process frame + rendered wheel; TTL 60/300/600/1800/3600 seconds from calculation start; lock serializes refresh |
| Transit BodyGraph | Rendered from the same frame, no second engine call; catalog text changes trigger rerendering |
| Gate forecast | In-process background search/results shared by viewers; reusable until the predicted transition is reached; error retries may restart after 60 seconds |
| Experiment samples | Up to 512 UTC timestamps for one hour per process, shared across timeline/ephemeris/series |
| Browser timeline | Up to 512 samples for one hour; at most one point request in flight per browser |
| Profile SVG and natal rows | Persistent, separate keys based on UTC birth instant, API URL and configured engine version |

Expired current transits refresh on the next request; autoreload is page reload at the chosen interval, not continuous motion. Failed refreshes show an error instead of silently presenting an old snapshot as current. Catalog wording is reread and cached graphics can be rerendered without new position calculations.

Experiment sampling allows one new calculation at a time within its own cache. A busy uncached `/frame` request returns 503 with `Retry-After`; cached points remain available. This is **not** a global lock covering forecast/current/natal requests, so those separate services can still load the upstream engine concurrently.

Timeline changes are debounced by 250 ms. New windows load boundaries/midpoint before progressively filling gaps. Obsolete queued requests are discarded; an in-flight request is allowed to finish but its obsolete response is not applied. Failed sampling stops further point loads until retry.

Persistent calculations write only on successful validation. Natal service rechecks the profile after upstream work and rejects results for edited/deleted profiles. Name/place-only edits preserve calculations; birth-time changes invalidate them.

All memory caches/locks are **per process**. Multiple Uvicorn workers do not share them and can duplicate upstream work. Database persistence is shared only if workers use the same database; it does not turn in-memory synchronization into distributed coordination.

## Accuracy and delivered limitations

- The inspected upstream endpoint rounds ecliptic longitude to three decimals. The WebUI's normalized values cannot restore omitted precision; engine correctness/version compatibility still needs verification.
- Gate-change forecasts search actual future API samples rather than linearly extrapolating. Adaptive steps consider both gate boundaries and retrograde motion. A detected change is bracketed to at most 30 seconds, with roughly ten years of search horizon and a 10,000-request budget. Very short crossings below sampling resolution can be missed. Missing changes, unsupported data and errors are explicit; estimates are not guaranteed exact timestamps.
- The timeline accepts timestamps in 1900–2099. The browser's minimum window is one hour; the planning API permits spans from one minute and requests of 16–160 samples. UI detail presets are 32/64/128/160. Range validation does not certify the engine's accuracy throughout that range.
- Timeline lines join only adjacent successful samples, not gaps or cyclic gate-axis wraps. Coarse windows can miss fast motion/short visits. Tooltip/table values are actual samples, not invented interpolated astronomical positions. Gate numbers are categories, not a physical magnitude.
- Ephemeris/series requests normalize start times to whole UTC hours and load on demand, with at most 31 timestamps.
- Gate-walk playback follows zodiac gate geometry endlessly, retains a bounded trail and visited-sector colors, and synchronizes zoom/pan between its two views. Its speed is a visual teaching control, not a planetary clock.
- General real-time/accelerated chronological planetary playback, automatic natal–transit channel synthesis, simultaneous multi-profile comparison and relocation/topocentric astrology are not implemented.
- There are no accounts/ownership checks, validated public hosting recipe or established PostgreSQL multi-user deployment. Birth data and current-profile selection are local installation state.

Astronomical position computation and Human Design rule mapping are distinct from validating interpretive claims about people or events. Neither the API nor these visualizations establish predictive or clinical validity.

## Proposed engineering developments

These are recommendations, not commitments or existing features:

1. **Formal provider boundary:** define a narrow protocol for timestamp positions, natal activations and SVG capability; keep upstream-specific schemas inside adapters. Add contract tests for precision, timestamps, failures and compatible versions before supporting another engine.
2. **Clear service decomposition:** move route orchestration out of the large app module while retaining transport, validation, domain derivation, persistence and presentation boundaries. The current ORM/service structure is a starting point, not a finished plugin architecture.
3. **Unified topology policy:** keep historical migrations frozen, but establish one tested runtime catalog of immutable topology and verify migration/database/renderer consistency. Keep mutable wording and personal notes separate from structural definitions.
4. **True transit playback:** implement explicit simulation time, pause/speed/return-to-now, measured prefetch windows and wrap-aware visual interpolation. Distinguish calculated gate/line values from interpolated marker motion; do not relabel gate-walk or page autoreload as this feature.
5. **Provenance and uncertainty:** record verified engine/build metadata and sampling/precision limits in exports or comparison views. Make narrow-window inspection and uncertainty more visible before adding interpretive summaries.
6. **Deployment hardening first:** before public or multi-user use, add authentication, ownership/authorization, secure transport, retention/export controls and database/concurrency tests. Introduce shared caching/job coordination only when measured multi-process demand warrants it.

Conceptual extensions and responsible practice are discussed separately in [English](practice.en.md) and [German](practice.de.md).
