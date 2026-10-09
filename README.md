# HDGUI

HDGUI is a local Python WebUI for exploring geocentric planetary positions alongside the Human Design gate system. It displays current transits, sampled gate timelines, persistent birth profiles and saved BodyGraph charts. Calculations come from a separate `humandesign_api` service; the WebUI manages presentation, local storage and caching.

**Status:** working local, single-user application, without login or profile ownership controls. Keep it on localhost. The interface currently includes German labels; maintained technical documentation is in English.

## Documentation

| Document | Purpose |
|---|---|
| [Operations](doc/operations.md) | Installation, configuration, Docker lifecycle, migrations, catalog imports, backup and troubleshooting |
| [Architecture](doc/architecture.md) | Actual implementation, domain boundaries, API integration, caches, limitations and proposed engineering work |
| [Practice — English](doc/practice.en.md) | Value, reflective workflows and possible future developments for astrology and Human Design practice |
| [Practice — German](doc/practice.de.md) | Equivalent conceptual guide in German |
| [Original planning notes](doc/todo.md) | Historical German user notes, preserved unchanged; not a maintained technical specification |

## Quick start

Requires Python **3.12+**, a virtual environment and Docker with daemon access for automatic calculation-service startup. Run from the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/alembic upgrade head
# Optional for a new database: explicitly select the public sample catalog.
# This replaces catalog names/descriptions; back up existing custom texts first.
.venv/bin/python -m app.catalog_fixture app/data/catalog-default.yaml
export HD_API_TOKEN='REPLACE_WITH_YOUR_LOCAL_API_TOKEN'
.venv/bin/python -m app
```

Open <http://127.0.0.1:8001>. Startup reuses a healthy local calculation service or creates/starts its Docker container. `./start.sh` is a convenience wrapper. Neither startup command runs migrations or imports catalog texts. For a separately managed service, see [Operations](doc/operations.md#startup-and-docker-lifecycle).

## What is delivered

- A current-transit BodyGraph (`/`) and zodiac/gate wheel (`/transit`), with timestamp, table, configurable cache and optional page autoreload.
- Future gate-change estimates based on API samples, including retrograde motion, with explicit sampling limits.
- Birth-profile creation, editing, deletion and persistent SVG charts; personality/design activations for one globally selected current profile.
- A zoomable, sampled gate timeline, ephemeris tables and transit series; optional fixed natal bands on the timeline.
- Linked catalogs of 64 gates, 36 channels and seven circuits, with explicitly imported local texts.
- A geometric gate-walk learning animation, **not** a planetary simulation.

Continuous real-time/accelerated chronological planetary playback, automatic natal–transit synthesis, simultaneous multi-profile comparison and public multi-user operation are **not delivered**. Proposed work is separated from existing behavior in the documentation.

Astrology and Human Design are interpretive frameworks, not established scientific explanations of personality or life events. Computed positions do not validate personal predictions; this application is not a diagnostic or treatment tool.

## Development and licensing

Existing validation commands:

```bash
.venv/bin/python -m unittest discover -s tests -q
node tests/test_gate_catalog.js
node tests/test_gate_timeline.js
```

The inherited BodyGraph layout is covered by **AGPL-3.0-or-later**: see [provenance](app/data/NOTICE) and [license text](app/data/LICENSE-AGPL). Review applicable obligations when deploying or distributing, and independently check the calculation service's license and terms. These documents do not relicense the project or upstream components.
