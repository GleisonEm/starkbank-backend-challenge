#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=infra/vps/lib.sh
. "$SCRIPT_DIR/lib.sh"

require_root
require_file "$VPS_ENV_FILE"

[ "$(stat -c '%u' "$VPS_ENV_FILE")" -eq 0 ] || fail "$VPS_ENV_FILE must be owned by root"
vps_mode=$(stat -c '%a' "$VPS_ENV_FILE")
case "$vps_mode" in
    600|640|644) ;;
    *) fail "$VPS_ENV_FILE must have mode 0600, 0640 or 0644" ;;
esac

load_vps_env

require_setting() {
    name=$1
    value=$2
    [ -n "$value" ] || fail "$name is required"
}

require_setting APP_IMAGE "$APP_IMAGE"
require_setting PUBLIC_HOST "$PUBLIC_HOST"
require_setting PUBLIC_BASE_URL "$PUBLIC_BASE_URL"
require_setting POSTGRES_DB "$POSTGRES_DB"
require_setting POSTGRES_USER "$POSTGRES_USER"
workspace_id=$(config_value "$VPS_ENV_FILE" STARKBANK_WORKSPACE_ID)
require_setting STARKBANK_WORKSPACE_ID "$workspace_id"
case "$workspace_id" in
    *[!0-9]*) fail "STARKBANK_WORKSPACE_ID must contain only digits" ;;
esac

case "$APP_IMAGE" in
    ghcr.io/gleisonem/starkbank-backend-challenge@sha256:*) ;;
    *) fail "APP_IMAGE must be the repository GHCR image pinned by sha256 digest" ;;
esac
digest=${APP_IMAGE##*@sha256:}
[ "${#digest}" -eq 64 ] || fail "APP_IMAGE digest must contain 64 hexadecimal characters"
case "$digest" in
    *[!0-9a-f]*) fail "APP_IMAGE digest is not lowercase hexadecimal" ;;
esac
[ "$PUBLIC_BASE_URL" = "https://$PUBLIC_HOST" ] || \
    fail "PUBLIC_BASE_URL must equal https://PUBLIC_HOST"

command -v docker >/dev/null 2>&1 || fail "docker is not installed"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is not installed"

printf 'VPS configuration files and required commands are valid.\n'
