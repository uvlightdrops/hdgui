#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if [[ ! -x .venv/bin/python ]]; then
    printf '%s\n' 'Python-Umgebung fehlt. Bitte zuerst die virtuelle Umgebung und Projektabhängigkeiten gemäß README installieren.' >&2
    exit 1
fi

exec .venv/bin/python -m app "$@"
