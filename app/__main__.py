"""Start the calculation service (if needed) and the WebUI with Uvicorn's standard options."""
import errno
import socket
import sys

import click
from uvicorn.main import main as uvicorn_cli

from app.backend import ensure_backend

APP_OPTIONS = [
    click.Option(
        ["--no-api"], is_flag=True, envvar="HDGUI_NO_API",
        help="Nur die WebUI starten; den Berechnungsdienst nicht prüfen oder starten.",
    ),
    click.Option(
        ["--api-timeout"], type=click.FloatRange(min=0), default=60.0, show_default=True,
        envvar="HDGUI_API_START_TIMEOUT",
        help="Sekunden, die auf die Bereitschaft des Berechnungsdienstes gewartet wird.",
    ),
]


def _check_frontend_address(params) -> None:
    host, port = params["host"], params["port"]
    if not 0 <= port <= 65535:
        raise click.ClickException("Port muss zwischen 0 und 65535 liegen.")
    # Socket activation and Unix sockets are bound directly by Uvicorn.
    if params["fd"] is not None or params["uds"] is not None:
        return
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                message = (
                    f"{host}:{port} ist bereits belegt. "
                    "Die WebUI oder ein anderer Dienst läuft bereits."
                )
            else:
                message = f"Startprüfung für {host}:{port} fehlgeschlagen: {exc}"
            raise click.ClickException(message) from exc


def _run(no_api, api_timeout, **uvicorn_params):
    _check_frontend_address(uvicorn_params)
    if not no_api:
        ensure_backend(api_timeout)
    return click.get_current_context().invoke(uvicorn_cli.callback, **uvicorn_params)


# A separate command reuses Uvicorn's parameter objects without modifying its CLI.
cli = click.Command(
    "python -m app",
    params=[*uvicorn_cli.params, *APP_OPTIONS],
    callback=_run,
    context_settings=dict(uvicorn_cli.context_settings),
    help="Startet bei Bedarf den lokalen Berechnungsdienst (Docker) und danach die WebUI. "
    "Alle Uvicorn-Optionen werden unterstützt.",
)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        with cli.make_context(
            "python -m app", ["app.main:app", *args],
            default_map={"host": "127.0.0.1", "port": 8001},
        ) as context:
            return cli.invoke(context)
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.exceptions.Exit as exc:
        return exc.exit_code
    except KeyboardInterrupt:
        click.echo("Abgebrochen.", err=True)
        return 130


if __name__ == "__main__":
    sys.exit(main())
