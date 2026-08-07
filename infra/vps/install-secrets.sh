#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=infra/vps/lib.sh
. "$SCRIPT_DIR/lib.sh"

require_root
"$SCRIPT_DIR/config-check.sh" >/dev/null
load_vps_env
command -v openssl >/dev/null 2>&1 || fail "openssl is not installed"
command -v tar >/dev/null 2>&1 || fail "tar is not installed"

umask 077
install -d -m 0700 -o root -g root "$SECRETS_ROOT"
receive_dir=$(mktemp -d)
release_dir=$(mktemp -d "$SECRETS_ROOT/.release.XXXXXX")
cleanup() {
    rm -rf "$receive_dir"
    [ -z "$release_dir" ] || rm -rf "$release_dir"
}
trap cleanup EXIT HUP INT TERM

tar --extract --file=- --directory="$receive_dir" \
    --no-same-owner --no-same-permissions -- \
    project-id private-key.pem postgres-password review-api-token

for incoming_file in project-id private-key.pem postgres-password review-api-token; do
    [ -f "$receive_dir/$incoming_file" ] || fail "secret archive is missing $incoming_file"
    [ ! -L "$receive_dir/$incoming_file" ] || fail "$incoming_file must be a regular file"
done

project_id=$(command cat "$receive_dir/project-id")
case "$project_id" in
    ''|*[!0-9]*) fail "Stark Bank Project ID must contain only digits" ;;
esac

private_key_file="$receive_dir/private-key.pem"
openssl pkey -in "$private_key_file" -check -noout >/dev/null 2>&1 || \
    fail "Stark Bank private key is not a valid PEM private key"

postgres_password=$(command cat "$receive_dir/postgres-password")
case "$postgres_password" in
    ''|*[!A-Za-z0-9._~-]*) \
        fail "PostgreSQL password must use URL-safe characters A-Z a-z 0-9 . _ ~ -" ;;
esac
[ "${#postgres_password}" -ge 24 ] || fail "PostgreSQL password must contain at least 24 characters"
[ "${#postgres_password}" -le 128 ] || fail "PostgreSQL password must contain at most 128 characters"

review_api_token=$(command cat "$receive_dir/review-api-token")
[ "${#review_api_token}" -ge 32 ] || fail "review API token must contain at least 32 characters"
if [ -f "$SECRETS_DIR/postgres-password" ]; then
    installed_postgres_password=$(command cat "$SECRETS_DIR/postgres-password")
    [ "$postgres_password" = "$installed_postgres_password" ] || \
        fail "PostgreSQL password rotation requires a coordinated database role update"
fi

printf '%s' "$postgres_password" > "$release_dir/postgres-password"
{
    printf 'DATABASE_URL=postgresql+psycopg://%s:%s@postgres:5432/%s\n' \
        "$POSTGRES_USER" "$postgres_password" "$POSTGRES_DB"
    printf 'STARKBANK_PROJECT_ID=%s\n' "$project_id"
    printf 'REVIEW_API_TOKEN=%s\n' "$review_api_token"
} > "$release_dir/runtime.env"

install -m 0400 -o 10001 -g 10001 "$private_key_file" \
    "$release_dir/starkbank-private-key.pem"
chmod 0600 "$release_dir/postgres-password" "$release_dir/runtime.env"
chown root:root "$release_dir/postgres-password" "$release_dir/runtime.env"

SECRETS_DIR="$release_dir" "$SCRIPT_DIR/validate-secrets.sh" >/dev/null

previous_release=''
if [ -L "$SECRETS_ROOT/current" ]; then
    previous_release=$(readlink "$SECRETS_ROOT/current")
fi
new_release=$(basename "$release_dir")
new_link="$SECRETS_ROOT/.current.$$"
ln -s "$new_release" "$new_link"
mv -Tf "$new_link" "$SECRETS_ROOT/current"
release_dir=''

case "$previous_release" in
    .release.*)
        case "$previous_release" in
            */*) fail "previous secret release target is invalid" ;;
        esac
        [ "$previous_release" = "$new_release" ] || \
            rm -rf "${SECRETS_ROOT:?}/$previous_release"
        ;;
esac

"$SCRIPT_DIR/validate-secrets.sh" >/dev/null
printf 'GitHub Environment secrets installed under %s.\n' "$SECRETS_ROOT"
