"""Persist the local backend secret without executing shell configuration."""
import os
from pathlib import Path
import secrets
import stat

import click

TOKEN_FILE = Path(__file__).resolve().parent.parent / ".hdgui-api-token"


def load_saved_token(env) -> None:
    if env.get("HD_API_TOKEN"):
        return
    try:
        fd = os.open(TOKEN_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise click.ClickException(f"Token-Datei {TOKEN_FILE} nicht lesbar: {exc.strerror}") from exc
    try:
        with os.fdopen(fd, encoding="utf-8") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise click.ClickException(
                    f"Token-Datei {TOKEN_FILE} muss eine eigene reguläre Datei mit Rechten 600 sein."
                )
            token = source.read(4097).removesuffix("\n")
    except (OSError, UnicodeError) as exc:
        raise click.ClickException(f"Token-Datei {TOKEN_FILE} konnte nicht gelesen werden.") from exc
    if not token or len(token) > 4096 or any(char.isspace() for char in token) or "\x00" in token:
        raise click.ClickException(f"Token-Datei {TOKEN_FILE} enthält keinen gültigen einzelnen Token.")
    env["HD_API_TOKEN"] = token


def create_saved_token(env) -> None:
    token = secrets.token_hex(32)
    try:
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        load_saved_token(env)
        return
    except OSError as exc:
        raise click.ClickException(f"Token-Datei {TOKEN_FILE} nicht anlegbar: {exc.strerror}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            os.fchmod(target.fileno(), 0o600)
            target.write(token + "\n")
            target.flush()
            os.fsync(target.fileno())
    except OSError as exc:
        raise click.ClickException(
            f"Token-Datei {TOKEN_FILE} konnte nicht gespeichert werden; Datei vor Neustart prüfen."
        ) from exc
    env["HD_API_TOKEN"] = token
    click.echo(f"API-Token erzeugt und in {TOKEN_FILE} gespeichert (Rechte 600).", err=True)
