# Operations

[Documentation index](../README.md#documentation) · [Architecture](architecture.md) · [Practice](practice.en.md)

This is the operational reference for the current **local, single-user** implementation. There is no login, profile ownership or server-side per-user isolation. Do not expose it publicly or to an untrusted shared network.

## API key: where it comes from (required before first start)

**The local API key is generated automatically on first setup.** It is a shared secret, not a key issued by Docker Hub or an external API provider. There is no registration step. The backend container and WebUI must use exactly the same **`HD_API_TOKEN`**. HDGUI passes it to a newly created container and sends it as a bearer token with calculation requests.

After installation below, simply run:

```bash
./start-backend.sh
./start.sh
```

Both scripts load the same **`.hdgui-api-token` file in the repository root**, even when started from different terminals. If no token is configured, no healthy service is found and the named container does not exist, startup generates a cryptographically random 256-bit token and saves it with **permissions `600`** (owner read/write only). The file is Git-ignored, is read as plain text (not executed), and must remain a regular file owned by the current user with permissions `600`. Invalid or insecure files cause an explicit error. The console reports the saved file's location, **never the token value**. Starting `./start.sh` alone also performs this setup if needed.

An explicitly exported, nonempty `HD_API_TOKEN` takes precedence over the file. Explicit environment tokens are not automatically saved. If you already have a backend, use its existing token; to enter it without putting its value into shell history and save it for both scripts:

```bash
read -rsp 'Saved HD_API_TOKEN: ' HD_API_TOKEN; printf '\n'
export HD_API_TOKEN
(
  umask 077
  set -o noclobber
  printf '%s\n' "$HD_API_TOKEN" > .hdgui-api-token
)
./start-backend.sh
./start.sh
```

Run the save command from the repository root; it refuses to overwrite an existing file. Protect and back up the token file separately. Neither script exports variables into its parent shell or loads a `.env` file. The direct Python entry points use the same token file and setup behavior.

**If a healthy service or an existing container is found without a locally known token, managed startup fails instead of generating a replacement.** Restore the previous token through the environment or token file. An existing container retains the token from its creation; changing the local file or shell variable does not update it. If that token is lost or must be rotated, follow the safe container-recreation procedure in [Startup and Docker lifecycle](#startup-and-docker-lifecycle). For a separately operated remote API, obtain the token from its operator and configure it explicitly instead. `--no-api` loads a saved token but never generates one or checks Docker.

**A healthy `/health` response does not validate the key.** A missing or mismatched token causes calculation failures even when the backend is running.

## Installation and updates

Use Python 3.12+ and run commands from the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/alembic upgrade head
```

Docker must be installed, on `PATH` and accessible to the user for automatic backend management. Alternatively, manage the calculation service yourself and start with `--no-api`.

Alembic revisions `0001`–`0004` create profiles/chart storage, UI settings, natal activations, the 64-gate/36-channel topology and seven circuits. A freshly migrated database has neutral catalog names and empty descriptions. **Migration does not import a text catalog.**

On updates, back up the database, update/install the project dependencies and run `.venv/bin/alembic upgrade head` against the same configured database before restarting. Revision `0004` adds circuit assignments while preserving existing profile and catalog texts. There is no need to downgrade or recreate a database already at `0003`.

**The expanded calculation cache requires revision `0005`.** After backing up and stopping the WebUI, run `.venv/bin/alembic upgrade head`, then restart. This adds `transit_samples` without changing existing profiles or catalog texts. Startup does not run migrations automatically; missing cache tables produce an explicit error.

## Configuration

Set environment variables in the shell or process manager before running migrations, imports or the application. The application does not automatically load a `.env` file.

| Variable | Default / effect |
|---|---|
| `HDGUI_DATABASE_URL` | `sqlite:///data/hdgui.db`; used by application, migration and catalog import |
| `HDGUI_API_URL` | `http://127.0.0.1:9021`; calculation-service base URL |
| `HD_API_TOKEN` | Explicit value takes precedence over `.hdgui-api-token`; generated and saved for a new local backend if neither is present; see [API key setup](#api-key-where-it-comes-from-required-before-first-start) |
| `HDGUI_ENGINE_VERSION` | `4.0.2`; cache identity marker, not automatic engine detection or an image-version pin |
| `HDGUI_SESSION_SECRET` | Random if unset; set a stable secret to retain valid form sessions across restarts |
| `HDGUI_API_CONTAINER` | `hd-api`; automatically managed container name |
| `HDGUI_API_IMAGE` | `dturkuler/humandesign_api:latest`; image used when creating a container |
| `HDGUI_API_START_TIMEOUT` | `60`; readiness-wait seconds, also settable with `--api-timeout` |
| `HDGUI_NO_API` | `1` skips backend checking/management, like `--no-api` |
| `UVICORN_HOST`, `UVICORN_PORT` | Standard Uvicorn overrides; application defaults are `127.0.0.1`, `8001` |

Relative SQLite and private fixture paths resolve from the working directory. Both shell scripts change to the repository directory; direct module commands should be run there too. Explicit Uvicorn command-line options take precedence over corresponding environment settings.

Keep secrets out of committed files, screenshots and logs. After an engine update, set `HDGUI_ENGINE_VERSION` to the deployed version so stored chart/activation keys change. The default Docker `latest` tag can change independently; choose a verified fixed tag/digest with `HDGUI_API_IMAGE` for reproducibility.

## Startup and Docker lifecycle

```bash
# Loads the saved token or generates one for a new local backend.
./start-backend.sh
# Optional readiness timeout override:
./start-backend.sh --api-timeout 120
# Then start the WebUI (also loads the saved token):
.venv/bin/python -m app
# Equivalent repository wrapper, with optional Uvicorn arguments:
./start.sh --reload --port 8001
```

`./start-backend.sh` starts **only the calculation backend**, using the same configuration and lifecycle logic as WebUI startup, and exits once the service is ready. It requires the installed `.venv` and Docker access when a container needs to be created or started. It reuses a healthy service, starts an existing stopped container or creates a missing container; it never automatically replaces one. `HDGUI_API_START_TIMEOUT` sets its default readiness timeout (60 seconds); `--api-timeout` overrides it. Docker errors and readiness failures exit nonzero. The equivalent direct command is `.venv/bin/python -m app.backend`.

Starting the backend separately is optional: `./start.sh` also manages it automatically.

The Python entry point accepts standard Uvicorn options plus `--no-api` and `--api-timeout`. Its default WebUI address is `127.0.0.1:8001`; the calculation service has a separate default port, `9021`.

Startup proceeds as follows:

1. Check whether the requested frontend address can be bound. An occupied address aborts startup without stopping any process or starting a backend. Uvicorn's final binding remains authoritative.
2. Check `GET /health` at `HDGUI_API_URL`. HTTP 200 JSON with `status: ok` is reused unless reported dependency states contain an error.
3. Otherwise, create the named Docker container if absent, start it if `created`/`exited`, or await readiness if `running`/`restarting`. Other states, such as `paused`, require manual attention.
4. Wait for readiness, normally up to 60 seconds. Docker failure, container exit or readiness timeout prevents frontend startup.

Creating the default container is equivalent to:

```bash
docker run -d --name hd-api -p 127.0.0.1:9021:9021 \
  -e HD_API_TOKEN dturkuler/humandesign_api:latest
```

Only the environment-variable **name**, not its token value, appears in the command arguments. Docker still stores container environment configuration; daemon access is privileged and must be restricted.

Automatic management accepts only plain `http://` loopback URLs (`127.0.0.1`, `localhost`, `[::1]`) without a non-root path, credentials, query or fragment. The host port follows `HDGUI_API_URL`; the container port remains 9021. For other URLs or a manually managed service:

```bash
.venv/bin/python -m app --no-api
```

This skips readiness checks; calculations can subsequently fail if the service is absent or incompatible. Profile data can be saved without a running calculation service. Remote API configuration sends calculation timestamps to that service: local-only privacy no longer applies, and this mode does not make the WebUI safe to publish.

Stopping the WebUI does **not** stop the backend:

```bash
docker stop hd-api
```

An existing container keeps its original token, image and port bindings. Changing environment variables does not modify it, and a healthy service is reused unchanged. `/health` is unauthenticated: readiness is **not** evidence that your token works.

If recreation is necessary, first inspect the container and preserve any user-added files/mounts. Only after confirming that removal is safe, stop it and remove it with `docker stop hd-api` followed by `docker rm hd-api`; the next application startup creates a new container using the current environment. Removal deletes the container's writable layer. Do not remove other installations or assume user-added data are disposable.

Neither Python startup command nor either shell script performs migrations or catalog import.

## Catalog imports and private fixtures

Catalog topology and catalog wording are separate. Import changes wording only; it cannot change gate centers, channel endpoints or circuit assignments.

The shipped `app/data/catalog-default.yaml` contains original short sample descriptions and complete catalog records. To deliberately choose the public sample:

```bash
.venv/bin/alembic upgrade head
.venv/bin/python -m app.catalog_fixture app/data/catalog-default.yaml
```

Without a path, resolution is:

1. `data/fixtures/catalog.yaml`, if present in the current working directory: a private local catalog.
2. Otherwise, the installed package's `app/data/catalog-default.yaml`, located relative to the module, not the shell directory.

```bash
.venv/bin/python -m app.catalog_fixture
.venv/bin/python -m app.catalog_fixture data/my-catalog.yaml
```

An explicit path is used exactly as supplied. Invalid explicit or selected private files fail; there is no silent fallback to public content. Private `data/` is ignored by Git, but ignores do not protect already tracked files or exported database copies. Keep licensed/private texts out of commits. A local `sources.yaml` may document provenance for your own use; it is **not** an import source. Historical profile-specific active/passive CSV notes are not treated as universal gate definitions.

### Schema and overwrite behavior

The UTF-8 YAML (optional BOM) must contain `schema_version: 2` and complete `gates`, `circuits`, `channels` lists:

| List | Required record fields |
|---|---|
| `gates` | `number`, `name`, `description`, `center` |
| `circuits` | `id`, `name`, `description` |
| `channels` | `gates: [a, b]`, `circuit_id`, `name`, `description` |

All 64 gates, seven circuits and 36 unordered canonical channel pairs must occur exactly once. Circuit IDs are `integration`, `centering`, `knowing`, `ego`, `defense`, `logic`, `sensing`. Centers and assignments must match the migrated database. Unknown fields, wrong types, duplicate YAML keys and invalid relationships are rejected. Circuit names are unique; required text is nonempty. Gate descriptions and channel names/descriptions may be `null`.

**Back up before importing into an existing installation.** Import is validated, atomic and idempotent for identical input, but is not a merge: it replaces all catalog names/descriptions, including clearing optional text with `null`. Reimporting the public sample overwrites custom wording. Profiles, saved SVGs, activations, settings and topology remain unchanged. Validation/database errors abort the whole transaction. Pages and tooltips use database texts on the next load, including rerendering cached views when texts change.

## Storage, time handling and global settings

### Personal center timeline

Select a profile under **Geburtsprofile → Als aktuell setzen**, then open **Zentren-Verlauf** (`/centers`). The left-aligned BodyGraph combines that profile's personality/design activations with the transit at the chosen UTC timestamp. Only the stacked tracks sit to its right; controls remain below both across the available width. Tracks can be switched on/off per center. On narrow screens the tracks stack below the graph. Set a start/end window and sample detail, use day/week/month presets or move backward/forward, and click a track or use the time slider to choose the graph's timestamp.

The working view omits introductory headings and explanatory blocks. Its legend and sampling notes live under **Wissen → Grundlagen** (`/knowledge#center-timeline`), linked below the timeline. **Tore** and **Kanäle** are grouped under the expandable **Wissen** menu; their existing URLs remain unchanged.

**Wissen → Himmelskörper** (`/knowledge/celestial-bodies`) provides a static table of the 13 usual activation sources, their glyphs and explicitly symbolic psychological-astrology interpretations. It requires neither a selected profile nor a calculation-service request.

A center counts as defined only through a **complete channel** in the combined gate set. Natal-defined centers remain active throughout the window. Bars show sampled states, not exact switch times; hatching means not yet calculated. The graph's status text lists defined centers at the selected timestamp. On failure, use **Zeitraum laden** to retry; after changing the current profile or its birth time in another tab, reload the page. Natal activations and transit samples are persisted; the expanded transit cache requires migration `0005`.

### Storage and time

Profiles, saved SVG charts, natal activations, catalog records, UI settings and reusable transit samples reside in the configured database. Current snapshot/render caches and completed gate-forecast results remain in memory; forecasts reuse persisted transit samples for their search calculations.

### Calculation cache

Transit samples used by center/gate timelines, ephemerides, series and gate-change searches are stored in `transit_samples`. The key combines the UTC second with a hash of API URL, declared engine version and cache-format version. Samples do not expire just because an hour passes or the WebUI restarts: the same ephemeris inputs are reusable. A RAM front cache holds 512 points for one hour; the database holds at most **20,000 samples across all versions**, removing the oldest inserted entries on new writes. This is a storage bound, not a time-resolution change.

Set `HDGUI_ENGINE_VERSION` after updating the calculation engine, even if the Docker tag remains `latest`; changed URL/version inputs use a new namespace rather than old results. Tokens are not stored in cache keys or payloads. Invalid cache payloads and database read/write failures are reported explicitly, not silently bypassed; failed calculations are not cached.

The center view additionally keeps up to **64 personal SVG renderings in server RAM**, keyed by actual transit/natal inputs, defined centers and current catalog text. Birth/activation or catalog changes therefore cannot reuse an incompatible drawing. In the browser, up to **512 center samples for one hour** survive window changes within the open page. Each window load still validates its first sample/profile against the server; the remaining matching points can be reused. A page reload clears that browser cache. Current-profile and birth-time checks still run on server requests, including SVG cache hits.

Cache rows are included in normal database backups. They contain planetary positions and sample times, not profile names or birth-profile links; sample times can nevertheless reflect user activity. Personal SVG caches exist only in process memory. If intentional recalculation or removal of sample history is needed, stop the WebUI and clear only `transit_samples` using your database administration tool; do not delete the whole database. Corrupt cache entries require explicit repair/removal. No Docker backend restart is required for this schema change.

`/settings` selects a transit cache interval of 1m, 5m, 10m, 30m or 1h and page autoreload; defaults are 1m with autoreload. These settings and the selected current profile are **global to the installation**, not browser/user preferences. Other open tabs pick up profile/settings changes when reloaded. Deleting the selected profile clears its selection.

Birth entry requires a local date/time, IANA timezone and place label. Nonexistent or ambiguous daylight-saving times are rejected. If the actual UTC time is known for an ambiguous local time, enter it with `Etc/UTC`; do not silently guess. Computations use the converted UTC instant at a zero-offset reference location to avoid an upstream fractional-timezone truncation problem. Place is retained as profile information, not used for relocated/topocentric calculations.

SVG and personality/design activations have separate persistent caches keyed by birth UTC, API URL and declared engine version. Birth-time changes invalidate them; name/place changes do not. Activations are calculated lazily for catalog highlights or the timeline overlay. Failed calculations do not overwrite stored results; a profile edited/deleted during natal calculation cannot receive the obsolete result.

## Backup and restoration

For the default SQLite installation, stop every WebUI process and any other process using the database before copying. The backend does not write this application database. Choose a new backup name so an earlier backup is not overwritten:

```bash
mkdir -p data/backups
cp -p data/hdgui.db data/backups/hdgui-before-update.db
```

If journal/WAL sidecars remain or other clients cannot be stopped, do not assume a main-file copy is consistent: use SQLite's supported backup mechanism to produce a consistent snapshot. For non-SQLite databases, use that database's native backup tooling.

Preserve private catalogs/provenance separately if you need to reproduce imports, plus securely stored deployment configuration. Database backups include sensitive birth data and imported private wording; restrict permissions and protect backup storage.

To restore, stop all writers, retain the current database as a separate backup, restore the chosen snapshot to the configured location, run migrations required by the installed application, then restart. Never mix a database snapshot with stale journal/WAL sidecars. Check profile access and catalog pages after restoration. Deletion in the UI does not erase older backups.

## Security and licensing

- Bind to loopback and limit access to the host/account. CSRF checks, strict same-site session cookies and SVG isolation are safeguards, **not** authentication.
- Obtain informed permission before storing another person's birth information; minimize identifying labels and agree on retention/deletion.
- Never send raw private fixtures or birth data to third-party services merely to troubleshoot. Review logs before sharing them.
- PostgreSQL is a possible SQLAlchemy backend with an appropriate driver, not a tested public multi-user deployment recipe. Authentication, ownership, authorization, HTTPS and deployment hardening remain required future work.
- The inherited layout uses AGPL-3.0-or-later: [NOTICE](../app/data/NOTICE), [license](../app/data/LICENSE-AGPL). Independently review calculation-service licensing and the rights to any imported texts.

## Troubleshooting

| Symptom | Safe next step |
|---|---|
| Missing `.venv` or package | Follow installation; the wrapper does not create/install it |
| Frontend address occupied | Inspect the existing service or choose another `--port`; do not kill unrelated processes |
| Missing tables/settings/topology | Check `HDGUI_DATABASE_URL` and working directory; run `.venv/bin/alembic current` then `upgrade head` |
| Docker unavailable/permission denied | Fix Docker access, or manage the backend separately and use `--no-api` |
| Readiness timeout/container exit | Check `docker ps -a` and `docker logs hd-api`; inspect port bindings and dependencies |
| Existing backend but local token missing | Restore the existing token into `HD_API_TOKEN` or `.hdgui-api-token` with permissions `600`; startup will not generate a replacement |
| Healthy backend but HTTP 401/403 | Verify token configuration on both sides, including any environment override; a reused container keeps its original token |
| Catalog import fails | Check selected path, schema version and completeness; correct the file, do not alter canonical topology |
| Public texts unexpectedly replace private texts | Restore the pre-import database backup; explicitly select the intended private catalog next time |
| Invalid form session after restart | Reload the form; use a stable `HDGUI_SESSION_SECRET` if persistence is needed |
| Timeline temporarily returns 503 | One new sample calculation is already running; the client retries using `Retry-After` |
| Forecast pending/missing or timeline coarse | Check backend errors and sampling limits in [Architecture](architecture.md#accuracy-and-delivered-limitations); zoom in rather than treating drawn lines as exact events |

Never diagnose by posting token values or full birth-profile/database dumps.
