#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

container=hd-api
image=dturkuler/humandesign_api:latest
token_file=.hdgui-api-token

if [[ -z "${HD_API_TOKEN:-}" ]]; then
    if [[ ! -f "$token_file" ]]; then
        printf 'HD_API_TOKEN fehlt; setze es oder lege %s an.\n' "$token_file" >&2
        exit 1
    fi
    chmod 600 "$token_file"
    IFS= read -r HD_API_TOKEN < "$token_file" || true
fi

if [[ -z "${HD_API_TOKEN:-}" ]]; then
    printf 'HD_API_TOKEN ist leer.\n' >&2
    exit 1
fi
export HD_API_TOKEN

if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' 'Docker ist nicht installiert.' >&2
    exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
    printf '%s\n' 'curl ist nicht installiert.' >&2
    exit 1
fi
docker info >/dev/null

if docker container inspect "$container" >/dev/null 2>&1; then
    if [[ "$(docker inspect --format '{{.State.Running}}' "$container")" != true ]]; then
        docker start "$container" >/dev/null
    fi
else
    docker run --detach --restart unless-stopped --name "$container" \
        --publish 127.0.0.1:9021:9021 \
        --env HD_API_TOKEN \
        "$image" >/dev/null
fi

for _ in {1..60}; do
    if response=$(curl --silent --fail --max-time 2 http://127.0.0.1:9021/health 2>/dev/null) \
        && grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' <<<"$response"; then
        printf 'Berechnungsdienst ist bereit: http://127.0.0.1:9021\n'
        exit 0
    fi
    if [[ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)" != true ]]; then
        printf 'Container %s läuft nicht. Details: docker logs %s\n' "$container" "$container" >&2
        exit 1
    fi
    sleep 1
done

printf 'Berechnungsdienst wurde nicht rechtzeitig bereit. Details: docker logs %s\n' "$container" >&2
exit 1
