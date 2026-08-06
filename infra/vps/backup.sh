#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=infra/vps/lib.sh
. "$SCRIPT_DIR/lib.sh"

require_root
load_vps_env
install -d -m 0700 -o root -g root "$BACKUP_DIR"
backup="$BACKUP_DIR/starkbank-trial-$(date -u +%Y%m%dT%H%M%SZ).dump"
compose exec -T postgres pg_dump \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --format custom > "$backup"
chmod 0600 "$backup"
printf '%s\n' "$backup"
