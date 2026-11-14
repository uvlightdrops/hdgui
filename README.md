# HDGUI

HDGUI is a local Python WebUI for exploring geocentric planetary positions alongside the Human Design gate system. It displays current transits, sampled gate timelines, persistent birth profiles and saved BodyGraph charts. Calculations come from a separate `humandesign_api` service; the WebUI manages presentation, local storage and caching.

**Status:** local single-user mode remains the default and must stay on localhost.
An opt-in private multi-user mode adds email-link sign-in, profile ownership,
accepted contacts and read-only sharing. Public deployment requires explicit
HTTPS/SMTP configuration, an external calculation API and ownership assignment
for existing data; see [Uberspace setup](doc/guides/uberspace.md). The interface
uses German labels; maintained technical documentation is in English.

## Documentation

| Document | Purpose |
|---|---|
| [Documentation Index](doc/README.md) | Navigation for architecture, features and guides |
| [Feature Catalog](doc/features/feature-catalog.md) | User-facing feature inventory and delivered limitations |
| [Operations](doc/guides/operations.md) | Installation, configuration, Docker lifecycle, migrations, catalog imports, backup and troubleshooting |
| [Architecture](doc/architecture/system-overview.md) | Actual implementation, domain boundaries, API integration, caches and limitations |
| [Knowledge Catalog Migration](doc/architecture/knowledge-catalog-migration.md) | Proposed unified multilingual knowledge model and migration approach |
| [Multi-user](doc/architecture/multi-user.md) | Account isolation, email sign-in, contacts, sharing and ownership migration |
| [Uberspace](doc/guides/uberspace.md) | U7/U8 user-service setup without local Docker |
| [Practice — English](doc/guides/practice.en.md) | Value, reflective workflows and possible future developments for astrology and Human Design practice |
| [Practice — German](doc/guides/practice.de.md) | Equivalent conceptual guide in German |

## Quick start

Requires Python **3.12+**, a virtual environment and Docker with daemon access for automatic calculation-service startup. Run from the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/alembic upgrade head
# Optional for a new database: explicitly select the public sample catalog.
# This replaces catalog names/descriptions; back up existing custom texts first.
.venv/bin/python -m app.catalog_fixture app/data/catalog-default.yaml
# Generates and saves a local API token if this is a new backend.
./start.sh
```

Open <http://127.0.0.1:8001>. Startup reuses a healthy local calculation service or creates/starts its Docker container. `./start-backend.sh` starts only the Docker backend. Neither script runs migrations or imports catalog texts. **Both scripts reuse `.hdgui-api-token` (Git-ignored, permissions `600`); a new local backend gets an automatically generated token without printing it.** An exported `HD_API_TOKEN` overrides the file. For an existing backend without a known token, restore its previous token rather than generating a new one. See [API key setup](doc/guides/operations.md#api-key-setup) and [Docker lifecycle](doc/guides/operations.md#startup-docker-lifecycle).

## What is delivered

Transit samples for timelines and gate-change searches are now persisted and reused across restarts. **Existing installations must run `.venv/bin/alembic upgrade head` before restarting the WebUI** (revision `0005`); see [calculation cache operations](doc/guides/operations.md#calculation-cache).

- A current-transit BodyGraph (`/`) and zodiac/gate wheel (`/transit`), with timestamp, table, configurable cache and optional page autoreload.
- Future gate-change estimates based on API samples, including retrograde motion, with explicit sampling limits.
- Birth-profile creation, editing, deletion and persistent SVG charts; personality/design activations for one globally selected current profile.
- A gate timeline with zoomable graphics and position tables (single timestamps, fixed intervals or graphic samples); optional fixed natal bands on the graphic.
- A personal center timeline (`/centers`): combined natal/transit BodyGraph at a selectable UTC timestamp, beside nine independently selectable sampled center-definition tracks, with controls below.
- Linked catalogs of 64 gates, 36 channels and seven circuits, plus a structured HD-entity catalog with separately imported, provenance-tracked texts.
- A geometric gate-walk learning animation, **not** a planetary simulation.

Opt-in multi-user mode provides any number of owned profiles, paginated private
lists, contact requests by verified email, revocable read-only grants, personal
current-profile/locale preferences and server-side revocable sessions. Recipients
cannot edit, delete, re-share or calculate missing natal results. Hosted operation
still requires staging validation, protected secrets and provider-specific logging
configuration; the Uberspace setup script has not been run on an actual target host.

Continuous real-time/accelerated chronological planetary playback, automatic
natal–transit synthesis and simultaneous multi-profile comparison are **not delivered**.

Astrology and Human Design are interpretive frameworks, not established scientific explanations of personality or life events. Computed positions do not validate personal predictions; this application is not a diagnostic or treatment tool.

## Development and licensing

Existing validation commands:

```bash
.venv/bin/python -m unittest discover -s tests -q
node tests/test_gate_catalog.js
node tests/test_gate_timeline.js
node tests/test_center_timeline.js
```

The inherited BodyGraph layout is covered by **AGPL-3.0-or-later**: see [provenance](app/data/NOTICE) and [license text](app/data/LICENSE-AGPL). Review applicable obligations when deploying or distributing, and independently check the calculation service's license and terms. These documents do not relicense the project or upstream components.
