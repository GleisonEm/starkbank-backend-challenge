#!/bin/sh
set -eu

REPOSITORY_ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$REPOSITORY_ROOT"

attempt=0
while [ "$attempt" -lt 30 ]; do
    url=$(docker compose --env-file .env -f compose.yaml -p starkbank-trial-local \
        logs --no-color cloudflared-quick 2>/dev/null \
        | grep -Eo 'https://[-a-z0-9]+\.trycloudflare\.com' \
        | tail -n 1 || true)
    if [ -n "$url" ]; then
        printf '%s\n' "$url"
        exit 0
    fi
    attempt=$((attempt + 1))
    sleep 1
done

printf 'Quick Tunnel URL was not found; inspect make logs.\n' >&2
exit 1
