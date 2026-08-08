#!/bin/sh

VPS_ENV_FILE=${VPS_ENV_FILE:-/etc/starkbank-trial/vps.env}
SECRETS_ROOT=${SECRETS_ROOT:-/etc/starkbank-trial/secrets}
SECRETS_DIR=${SECRETS_DIR:-$SECRETS_ROOT/current}
STATE_DIR=/var/lib/starkbank-trial
BACKUP_DIR=/var/backups/starkbank-trial
REPOSITORY_ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)

fail() {
    printf 'VPS configuration error: %s\n' "$1" >&2
    exit 1
}

require_root() {
    [ "$(id -u)" -eq 0 ] || fail "run this command with sudo"
}

require_file() {
    [ -f "$1" ] || fail "missing $1"
}

load_vps_env() {
    require_file "$VPS_ENV_FILE"
    COMPOSE_PROJECT_NAME=$(config_value "$VPS_ENV_FILE" COMPOSE_PROJECT_NAME)
    APP_IMAGE=$(config_value "$VPS_ENV_FILE" APP_IMAGE)
    PUBLIC_HOST=$(config_value "$VPS_ENV_FILE" PUBLIC_HOST)
    PUBLIC_BASE_URL=$(config_value "$VPS_ENV_FILE" PUBLIC_BASE_URL)
    POSTGRES_DB=$(config_value "$VPS_ENV_FILE" POSTGRES_DB)
    POSTGRES_USER=$(config_value "$VPS_ENV_FILE" POSTGRES_USER)
}

config_value() {
    file=$1
    key=$2
    awk -F= -v key="$key" '
        $1 == key {
            sub(/^[^=]*=/, "")
            value = $0
        }
        END { print value }
    ' "$file"
}

compose() {
    docker compose --env-file "$VPS_ENV_FILE" \
        -f "$REPOSITORY_ROOT/compose.vps.yaml" \
        -p starkbank-trial-vps "$@"
}
