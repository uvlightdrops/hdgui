---
project: hdgui
scope: guides
created: 2026-11-14 20
updated: 2026-11-14 20
update_reason: Describe native U7/U8 setup with email authentication and an external HTTPS calculation API.
status: draft
agent_project: hdgui
---

# Uberspace

[Guides](README.md) · [Multi-user design](../architecture/multi-user.md)

HDGUI runs natively in a Python virtual environment on Uberspace; Docker is
needed only on the separate calculation host. U7 uses Supervisord, U8 user
Systemd services. This guide and setup script have not been executed on a
real Uberspace account; validate them on your staging account before release.

## Prerequisites

Use Python 3.12+, a repository clone in your home directory (not the public
document root), an HTTPS domain for HDGUI, and SMTP credentials capable of
sending login messages. The external calculation API needs a verified HTTPS
endpoint and the same API token configured on that server.

The WebUI listens on `0.0.0.0:8001`, because Uberspace's web backend cannot
proxy to a loopback-only listener. Do not expose the calculation API through
the WebUI. Keep the application on one worker initially; SQLite is sufficient
only while measured load and backup/restore procedures remain acceptable.

Before migration or updates, stop the service and back up the database and
private runtime configuration. The setup script runs Alembic migrations; it
does not back up data or overwrite an existing service definition.

## Private configuration

Create `~/etc/hdgui-runtime.json` outside the repository using a local editor
with restrictive creation permissions (`umask 077`). It must be a regular file
owned by your account with mode `600`; the script refuses less restrictive files.
Never commit or publish this file. All JSON values must be strings.

```json
{
  "HDGUI_PUBLIC_BASE_URL": "https://hdgui.example.org",
  "HDGUI_DATABASE_URL": "sqlite:////home/ACCOUNT/hdgui/data/hdgui.db",
  "HDGUI_SESSION_SECRET": "REPLACE_WITH_A_RANDOM_SECRET",
  "HDGUI_API_URL": "https://calculation.example.org",
  "HD_API_TOKEN": "REPLACE_WITH_THE_BACKEND_TOKEN",
  "HDGUI_ENGINE_VERSION": "4.0.2",
  "HDGUI_SMTP_HOST": "SMTP_HOST",
  "HDGUI_SMTP_PORT": "465",
  "HDGUI_SMTP_FROM": "hdgui@example.org",
  "HDGUI_SMTP_USERNAME": "SMTP_LOGIN",
  "HDGUI_SMTP_PASSWORD": "REPLACE_WITH_SMTP_PASSWORD",
  "HDGUI_SMTP_USE_SSL": "1"
}
```

Replace every placeholder. `HDGUI_SMTP_USE_SSL=1` uses implicit TLS, normally
port 465; `0` uses mandatory STARTTLS, normally port 587. Obtain the exact
host and credentials from your mail provider. The script always enables
`HDGUI_MULTI_USER=1` and `HDGUI_NO_API=1`; it reads the protected JSON itself,
without executing it as shell code. Login links use the configured base URL,
not a browser-supplied Host header.

Do not enable test-mail or insecure-localhost options on the public service.
Missing SMTP, base URL or calculation credentials must be corrected before
starting. Backend failures are not replaced with invented chart results.

## Install and connect

From the repository directory, choose the target platform:

```bash
# Uberspace 7:
python3.12 scripts/deploy/uberspace.py install \
  --platform u7 --config ~/etc/hdgui-runtime.json

# Alternatively, Uberspace 8:
python3.12 scripts/deploy/uberspace.py install \
  --platform u8 --config ~/etc/hdgui-runtime.json
```

This creates `.venv`, installs dependencies, applies migrations and writes
`~/etc/services.d/hdgui.ini` on U7 or
`~/.config/systemd/user/hdgui.service` on U8. It refuses to overwrite existing
service files. The equivalent U7 template is
[hdgui-u7.ini.example](../../deploy/uberspace/hdgui-u7.ini.example); replace
its account and checkout paths if using it manually. Inspect the generated
definition before starting:

```bash
# U7:
supervisorctl reread
supervisorctl update
supervisorctl status hdgui

# U8:
systemctl --user daemon-reload
systemctl --user enable --now hdgui
systemctl --user status hdgui
```

Register the application's web backend on its dedicated domain:

```bash
uberspace web backend set / --http --port 8001
uberspace web backend list
```

Configure the HTTPS domain using Uberspace's domain/certificate tooling.
Check that unauthenticated browser visits go to login and that no profile,
SVG, analysis or overlay is accessible without a session. Check SMTP delivery,
confirmation POST, logout and HTTPS cookies before inviting other users.

## Assign existing profiles

New installations have no legacy profiles. For an existing database, the
application permits sign-in but blocks ordinary pages until all old profiles
have an explicitly selected owner. It never assigns them to the first signup.

The intended owner signs in and verifies their email. Then the operator runs:

```bash
python3.12 scripts/deploy/uberspace.py assign-owner \
  --config ~/etc/hdgui-runtime.json --owner VERIFIED_OWNER_EMAIL
```

The command assigns only unowned profiles and copies the old singleton
preferences to that account. It refuses unverified or missing accounts.
Existing IDs, saved charts and natal data remain intact. Verify the owner
and profile inventory before giving other users access.

## Updates and operation

Stop the U7/U8 service, back up and pull the repository. Then update using the
same private configuration and restart the existing service:

```bash
python3.12 scripts/deploy/uberspace.py update --config ~/etc/hdgui-runtime.json
# U7:
supervisorctl restart hdgui
# U8:
systemctl --user restart hdgui
```

The update command reinstalls dependencies and applies migrations without
rewriting the service definition.

The runtime JSON is reread on every service start. Do not change its database
path accidentally. Preserve stable secrets, restrict access to logs and set
retention policies for profiles, security events, caches and backups. Remove
expired tokens and expired/revoked sessions older than a selected cutoff:

```bash
python3.12 scripts/deploy/uberspace.py prune \
  --config ~/etc/hdgui-runtime.json --before 2026-11-01T00:00:00Z
```

This leaves profiles and security events intact. Application access logging is
disabled in multi-user mode. Uberspace/proxy access logs must not record `/auth/confirm` query strings:
these temporarily carry email-link bearer tokens. Check provider logging
configuration; application `Referrer-Policy` cannot redact an upstream log.

The calculation host receives the required birth timestamps. Its token,
TLS configuration, logging, resource limits and retention are managed
separately. Account email addresses and contact/profile labels are not sent
to the calculation API.

Reference: [U7 Python](https://manual.uberspace.de/en/lang-python/),
[U8 Python](https://u8manual.uberspace.de/lang_python/),
[U7 web backends](https://manual.uberspace.de/en/web-backends/),
[U8 web backends](https://u8manual.uberspace.de/web_backends/).
