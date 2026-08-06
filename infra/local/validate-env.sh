#!/bin/sh
set -eu

REPOSITORY_ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
ENV_FILE="$REPOSITORY_ROOT/.env"
MODE=${1:-app}

fail() {
    printf 'configuration error: %s\n' "$1" >&2
    exit 1
}

env_value() {
    key=$1
    if printenv "$key" >/dev/null 2>&1; then
        printenv "$key"
        return
    fi
    awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); value=$0} END {print value}' "$ENV_FILE"
}

[ -f "$ENV_FILE" ] || fail "missing .env; run make env-init"

for key in COMPOSE_PROJECT_NAME POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD \
    STARKBANK_ENVIRONMENT STARKBANK_PROJECT_ID STARKBANK_WORKSPACE_ID \
    LOCAL_STARKBANK_PRIVATE_KEY_FILE; do
    value=$(env_value "$key")
    [ -n "$value" ] || fail "$key is required in .env"
done

[ "$(env_value STARKBANK_ENVIRONMENT)" = "sandbox" ] || \
    fail "STARKBANK_ENVIRONMENT must be sandbox"
[ "$(env_value POSTGRES_PASSWORD)" != "change-local-password" ] || \
    fail "replace the example POSTGRES_PASSWORD"

project_id=$(env_value STARKBANK_PROJECT_ID)
case "$project_id" in
    *[!0-9]*) fail "STARKBANK_PROJECT_ID must contain only digits" ;;
esac
workspace_id=$(env_value STARKBANK_WORKSPACE_ID)
case "$workspace_id" in
    ''|*[!0-9]*) fail "STARKBANK_WORKSPACE_ID must contain only digits" ;;
esac

key_file=$(env_value LOCAL_STARKBANK_PRIVATE_KEY_FILE)
case "$key_file" in
    /*) key_path=$key_file ;;
    *) key_path="$REPOSITORY_ROOT/$key_file" ;;
esac
[ -f "$key_path" ] || fail "private key file not found at LOCAL_STARKBANK_PRIVATE_KEY_FILE"
grep -q '^-----BEGIN .*PRIVATE KEY-----$' "$key_path" || fail "private key is not PEM encoded"

if [ "$MODE" = "sandbox" ]; then
    [ "$(env_value STARKBANK_SANDBOX_LIVE_ENABLED)" = "true" ] || \
        fail "set STARKBANK_SANDBOX_LIVE_ENABLED=true for explicit Sandbox calls"
    public_base_url=$(env_value PUBLIC_BASE_URL)
    case "$public_base_url" in
        https://*) ;;
        *) fail "PUBLIC_BASE_URL must be a public https URL" ;;
    esac
fi

printf 'Local configuration is valid for %s operations.\n' "$MODE"
