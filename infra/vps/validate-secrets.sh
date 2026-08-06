#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=infra/vps/lib.sh
. "$SCRIPT_DIR/lib.sh"

require_root
load_vps_env
command -v openssl >/dev/null 2>&1 || fail "openssl is not installed"

[ -d "$SECRETS_ROOT" ] || fail "missing $SECRETS_ROOT; run the approved GitHub deployment"
[ "$(stat -c '%u' "$SECRETS_ROOT")" -eq 0 ] || fail "$SECRETS_ROOT must be owned by root"
[ "$(stat -c '%a' "$SECRETS_ROOT")" -eq 700 ] || fail "$SECRETS_ROOT must have mode 0700"

if [ "$SECRETS_DIR" = "$SECRETS_ROOT/current" ]; then
    [ -L "$SECRETS_DIR" ] || fail "missing $SECRETS_DIR; run the approved GitHub deployment"
    [ "$(stat -c '%u' "$SECRETS_DIR")" -eq 0 ] || fail "$SECRETS_DIR must be owned by root"
    current_release=$(readlink "$SECRETS_DIR")
    case "$current_release" in
        .release.*) ;;
        *) fail "$SECRETS_DIR must target a managed secret release" ;;
    esac
    case "$current_release" in
        */*) fail "$SECRETS_DIR target must not contain a path separator" ;;
    esac
fi
[ -d "$SECRETS_DIR" ] || fail "missing $SECRETS_DIR; run the approved GitHub deployment"
[ "$(stat -Lc '%u' "$SECRETS_DIR")" -eq 0 ] || fail "$SECRETS_DIR must be owned by root"
[ "$(stat -Lc '%a' "$SECRETS_DIR")" -eq 700 ] || fail "$SECRETS_DIR must have mode 0700"

for secret_file in runtime.env postgres-password; do
    secret_path="$SECRETS_DIR/$secret_file"
    require_file "$secret_path"
    [ ! -L "$secret_path" ] || fail "$secret_path must not be a symbolic link"
    [ "$(stat -c '%u' "$secret_path")" -eq 0 ] || fail "$secret_path must be owned by root"
    [ "$(stat -c '%a' "$secret_path")" -eq 600 ] || fail "$secret_path must have mode 0600"
done

private_key_path="$SECRETS_DIR/starkbank-private-key.pem"
require_file "$private_key_path"
[ ! -L "$private_key_path" ] || fail "$private_key_path must not be a symbolic link"
[ "$(stat -c '%u' "$private_key_path")" -eq 10001 ] || \
    fail "$private_key_path must be owned by container UID 10001"
[ "$(stat -c '%g' "$private_key_path")" -eq 10001 ] || \
    fail "$private_key_path must be owned by container GID 10001"
[ "$(stat -c '%a' "$private_key_path")" -eq 400 ] || \
    fail "$private_key_path must have mode 0400"

runtime_env="$SECRETS_DIR/runtime.env"
[ "$(grep -c '^DATABASE_URL=' "$runtime_env")" -eq 1 ] || \
    fail "$runtime_env must contain one DATABASE_URL"
[ "$(grep -c '^STARKBANK_PROJECT_ID=' "$runtime_env")" -eq 1 ] || \
    fail "$runtime_env must contain one STARKBANK_PROJECT_ID"
[ "$(wc -l < "$runtime_env" | tr -d ' ')" -eq 2 ] || \
    fail "$runtime_env must contain only DATABASE_URL and STARKBANK_PROJECT_ID"

project_id=$(config_value "$runtime_env" STARKBANK_PROJECT_ID)
case "$project_id" in
    ''|*[!0-9]*) fail "STARKBANK_PROJECT_ID must contain only digits" ;;
esac

postgres_password=$(command cat "$SECRETS_DIR/postgres-password")
case "$postgres_password" in
    ''|*[!A-Za-z0-9._~-]*) fail "stored PostgreSQL password is invalid" ;;
esac
[ "${#postgres_password}" -ge 24 ] || fail "stored PostgreSQL password is too short"
[ "${#postgres_password}" -le 128 ] || fail "stored PostgreSQL password is too long"

expected_database_url="postgresql+psycopg://$POSTGRES_USER:$postgres_password@postgres:5432/$POSTGRES_DB"
[ "$(config_value "$runtime_env" DATABASE_URL)" = "$expected_database_url" ] || \
    fail "DATABASE_URL does not match VPS database configuration"

openssl pkey -in "$private_key_path" -check -noout >/dev/null 2>&1 || \
    fail "stored Stark Bank private key is invalid"

printf 'VPS runtime secret files are valid.\n'
