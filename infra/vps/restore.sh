#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=infra/vps/lib.sh
. "$SCRIPT_DIR/lib.sh"

require_root
load_vps_env
backup=${1:-}
[ -n "$backup" ] || fail "pass an absolute backup file path"
case "$backup" in
    "$BACKUP_DIR"/*.dump) ;;
    *) fail "backup must be a .dump file under $BACKUP_DIR" ;;
esac
[ -f "$backup" ] || fail "backup file does not exist"

compose stop api worker scheduler
restart_services() {
    compose up --detach --wait --wait-timeout 120 api worker scheduler caddy || true
}
trap restart_services EXIT HUP INT TERM
compose exec -T postgres pg_restore \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --clean \
    --if-exists \
    --no-owner \
    --exit-on-error < "$backup"
compose run --rm migrate
restart_services
trap - EXIT HUP INT TERM
"$SCRIPT_DIR/health.sh"
printf 'Restored %s\n' "$backup"
