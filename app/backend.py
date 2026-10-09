"""Ensure the local Human Design calculation service runs before the WebUI starts."""
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import click
import httpx

DEFAULT_API_URL = "http://127.0.0.1:9021"
DEFAULT_CONTAINER = "hd-api"
DEFAULT_IMAGE = "dturkuler/humandesign_api:latest"
CONTAINER_PORT = 9021
LOOPBACK_HOSTS = {"127.0.0.1": "127.0.0.1", "localhost": "127.0.0.1", "::1": "::1"}
DOCKER_TIMEOUT = 30
PROBE_TIMEOUT = 2.0
POLL_INTERVAL = 0.5


class BackendError(click.ClickException):
    pass


@dataclass(frozen=True)
class BackendConfig:
    api_url: str
    bind_host: str
    port: int
    container: str
    image: str

    @property
    def health_url(self) -> str:
        return f"{self.api_url.rstrip('/')}/health"

    @property
    def publish(self) -> str:
        host = f"[{self.bind_host}]" if ":" in self.bind_host else self.bind_host
        return f"{host}:{self.port}:{CONTAINER_PORT}"


def load_config(environ=None) -> BackendConfig:
    """Accept only plain loopback HTTP URLs; anything else is not ours to launch."""
    env = os.environ if environ is None else environ
    api_url = env.get("HDGUI_API_URL") or DEFAULT_API_URL
    refuse = (
        f"HDGUI_API_URL={api_url} ist kein lokaler HTTP-Dienst auf localhost; "
        "automatischer Start des Berechnungsdienstes nicht möglich. "
        "Dienst selbst betreiben und die WebUI mit --no-api starten."
    )
    try:
        parts = urlsplit(api_url)
        port = parts.port or 80
    except ValueError as exc:
        raise BackendError(refuse) from exc
    bind_host = LOOPBACK_HOSTS.get((parts.hostname or "").lower())
    if (
        parts.scheme != "http" or bind_host is None or parts.username or parts.password
        or parts.path not in ("", "/") or parts.query or parts.fragment or not 0 < port <= 65535
    ):
        raise BackendError(refuse)
    return BackendConfig(
        api_url=api_url,
        bind_host=bind_host,
        port=port,
        container=env.get("HDGUI_API_CONTAINER") or DEFAULT_CONTAINER,
        image=env.get("HDGUI_API_IMAGE") or DEFAULT_IMAGE,
    )


def probe_health(url: str) -> tuple[bool, str]:
    """Return readiness and a short reason, based on the unauthenticated /health endpoint."""
    try:
        response = httpx.get(url, timeout=PROBE_TIMEOUT)
    except httpx.HTTPError as exc:
        return False, f"nicht erreichbar ({type(exc).__name__})"
    if response.status_code != 200:
        return False, f"HTTP {response.status_code}"
    try:
        data = response.json()
    except ValueError:
        return False, "keine JSON-Antwort"
    if not isinstance(data, dict) or data.get("status") != "ok":
        return False, "Status nicht 'ok'"
    dependencies = data.get("dependencies")
    if isinstance(dependencies, dict):
        failed = sorted(name for name, state in dependencies.items() if state == "error")
        if failed:
            return False, f"Abhängigkeit meldet Fehler: {', '.join(failed)}"
    return True, "ok"


def _docker(*args: str, timeout: float | None = DOCKER_TIMEOUT, env=None) -> str:
    """Run a docker CLI command without a shell; secrets are only passed by variable name."""
    command = ["docker", *args]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False,
            env=None if env is None else dict(env),
        )
    except FileNotFoundError as exc:
        raise BackendError(
            "Docker wurde nicht gefunden. Docker installieren oder die WebUI mit --no-api "
            "starten und den Berechnungsdienst separat betreiben."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise BackendError(f"`docker {args[0]}` hat nicht innerhalb von {timeout:g} s geantwortet.") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:500] or f"Exit-Code {result.returncode}"
        raise BackendError(f"`docker {args[0]}` fehlgeschlagen: {detail}")
    return result.stdout.strip()


def container_state(name: str) -> str | None:
    output = _docker("ps", "--all", "--filter", f"name=^/{name}$", "--format", "{{.State}}")
    states = [line.strip().lower() for line in output.splitlines() if line.strip()]
    return states[0] if states else None


def wait_until_ready(config: BackendConfig, timeout: float, check_container: bool = True) -> None:
    deadline = time.monotonic() + timeout
    reason = "nicht geprüft"
    while True:
        ready, reason = probe_health(config.health_url)
        if ready:
            return
        if check_container:
            state = container_state(config.container)
            if state not in ("running", "restarting"):
                raise BackendError(
                    f"Container {config.container} läuft nicht mehr (Zustand: {state or 'fehlt'}). "
                    f"Details: docker logs {config.container}"
                )
        if time.monotonic() >= deadline:
            raise BackendError(
                f"Berechnungsdienst unter {config.health_url} nach {timeout:g} s nicht bereit "
                f"({reason}). Portzuordnung und `docker logs {config.container}` prüfen; "
                "die WebUI wird nicht gestartet."
            )
        time.sleep(POLL_INTERVAL)


def ensure_backend(timeout: float, environ=None) -> None:
    """Reuse a healthy service, start the named container, or create it as configured."""
    env = os.environ if environ is None else environ
    config = load_config(env)
    say = lambda message: click.echo(message, err=True)  # noqa: E731

    ready, _ = probe_health(config.health_url)
    if ready:
        say(f"Berechnungsdienst unter {config.api_url} ist bereit und wird weiterverwendet.")
        _warn_token(env, say)
        return

    if shutil.which("docker") is None:
        raise BackendError(
            f"Berechnungsdienst unter {config.api_url} ist nicht bereit und Docker wurde nicht "
            "gefunden. Docker installieren oder mit --no-api ohne Backend-Verwaltung starten."
        )
    state = container_state(config.container)
    if state is None:
        if not env.get("HD_API_TOKEN"):
            raise BackendError(
                f"Container {config.container} existiert nicht. Zum Anlegen muss HD_API_TOKEN "
                "in der Umgebung gesetzt sein."
            )
        say(
            f"Lege Container {config.container} aus {config.image} an "
            f"({config.publish}); das Image wird bei Bedarf heruntergeladen …"
        )
        # `-e HD_API_TOKEN` copies the value from this process' environment, never via argv.
        _docker(
            "run", "--detach", "--name", config.container, "--publish", config.publish,
            "--env", "HD_API_TOKEN", config.image, timeout=None, env=env,
        )
    elif state in ("exited", "created"):
        say(f"Starte vorhandenen Container {config.container} …")
        _docker("start", config.container)
        _warn_existing(config, say)
    elif state in ("running", "restarting"):
        say(f"Container {config.container} läuft, warte auf Bereitschaft …")
        _warn_existing(config, say)
    else:
        raise BackendError(
            f"Container {config.container} ist im Zustand '{state}' und wird nicht automatisch "
            "verändert. Bitte manuell prüfen (docker ps -a)."
        )

    wait_until_ready(config, timeout)
    say(f"Berechnungsdienst unter {config.api_url} ist bereit. Der Container läuft nach dem "
        "Beenden der WebUI weiter.")
    _warn_token(env, say)


def _warn_existing(config: BackendConfig, say) -> None:
    say(
        f"Hinweis: {config.container} nutzt Token und Portzuordnung aus seiner Erstellung. "
        f"Nach Änderungen Container manuell neu anlegen (docker rm -f {config.container})."
    )


def _warn_token(env, say) -> None:
    if not env.get("HD_API_TOKEN"):
        say("Warnung: HD_API_TOKEN ist nicht gesetzt; Berechnungen der WebUI werden fehlschlagen.")
    else:
        say("Hinweis: /health ist ohne Anmeldung erreichbar; ob HD_API_TOKEN passt, zeigt "
            "erst die erste Berechnung.")
