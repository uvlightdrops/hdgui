#!/usr/bin/env python3
"""Install or run HDGUI as an U7/U8 user service, without a Docker daemon."""
import argparse
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def load_environment(path):
    path = path.expanduser().resolve()
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("Die Laufzeitkonfiguration muss eine eigene reguläre Datei mit Modus 600 sein.")
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not all(
        isinstance(key, str) and isinstance(value, str) and
        (key.startswith("HDGUI_") or key == "HD_API_TOKEN") and "\0" not in value
        for key, value in data.items()
    ):
        raise ValueError("Konfiguration benötigt ausschließlich HDGUI_*- und HD_API_TOKEN-Zeichenketten.")
    os.environ.update(data)
    os.environ["HDGUI_MULTI_USER"] = "1"
    os.environ["HDGUI_NO_API"] = "1"
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "update", "run", "assign-owner", "prune"))
    parser.add_argument("--platform", choices=("u7", "u8"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--python", default="python3.12")
    parser.add_argument("--owner", help="Bereits per E-Mail verifizierter Erstbesitzer")
    parser.add_argument("--before", help="Zeitzonenbehafteter ISO-8601-Stichtag für prune")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Port muss zwischen 1024 und 65535 liegen.")
    try:
        config = load_environment(args.config)
        python = ROOT / ".venv/bin/python"
        if args.action == "assign-owner":
            if not args.owner:
                parser.error("--owner ist erforderlich.")
            return subprocess.run([str(python), "-m", "app.accounts", "--assign-owner", args.owner],
                                  cwd=ROOT, check=True).returncode
        if args.action == "prune":
            if not args.before:
                parser.error("--before ist erforderlich.")
            return subprocess.run([str(python), "-m", "app.accounts", "--prune-before", args.before],
                                  cwd=ROOT, check=True).returncode
        if args.action == "run":
            os.chdir(ROOT)
            os.execv(str(python), [
                str(python), "-m", "app", "--no-api", "--no-access-log", "--host", "0.0.0.0", "--port", str(args.port),
            ])
        if args.action == "install" and args.platform is None:
            parser.error("Installation benötigt --platform u7 oder u8.")
        subprocess.run([args.python, "-m", "venv", str(ROOT / ".venv")], check=True)
        subprocess.run([str(python), "-m", "pip", "install", "-e", str(ROOT)], check=True)
        subprocess.run([str(python), "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True)
        subprocess.run([str(python), "-c", "import app.main"],
                       cwd=ROOT, check=True)
        if args.action == "update":
            print("Abhängigkeiten und Migrationen aktualisiert. Dienst jetzt neu starten.")
            return 0
        command = [
            str(python), str(Path(__file__).resolve()), "run",
            "--config", str(config), "--port", str(args.port),
        ]
        if args.platform == "u7":
            folder = Path.home() / "etc/services.d"
            folder.mkdir(parents=True, exist_ok=True)
            service = folder / "hdgui.ini"
            content = (
                "[program:hdgui]\n"
                f"command={shlex.join(command).replace('%', '%%')}\n"
                f"directory={str(ROOT).replace('%', '%%')}\n"
                "autostart=yes\nautorestart=yes\nstartsecs=5\n"
                "redirect_stderr=true\nstdout_logfile=AUTO\n"
            )
        else:
            folder = Path.home() / ".config/systemd/user"
            folder.mkdir(parents=True, exist_ok=True)
            service = folder / "hdgui.service"
            quoted = " ".join(json.dumps(arg.replace("%", "%%").replace("$", "$$")) for arg in command)
            content = (
                "[Unit]\nDescription=HDGUI private multi-user web application\n\n"
                "[Service]\nType=simple\n"
                f"WorkingDirectory={json.dumps(str(ROOT).replace('%', '%%'))}\n"
                f"ExecStart={quoted}\nRestart=on-failure\nRestartSec=5\nUMask=0077\n\n"
                "[Install]\nWantedBy=default.target\n"
            )
        with service.open("x") as target:
            target.write(content)
        print(f"Dienstdefinition angelegt: {service}")
        print("Vor dem Start bestehende Profile ausdrücklich einem verifizierten Erstbesitzer zuordnen.")
        print("U7: supervisorctl reread && supervisorctl update")
        print("U8: systemctl --user daemon-reload && systemctl --user enable --now hdgui")
        print(f"Web-Backend: uberspace web backend set / --http --port {args.port}")
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Einrichtung fehlgeschlagen: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
