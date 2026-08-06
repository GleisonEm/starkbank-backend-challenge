#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=infra/vps/lib.sh
. "$SCRIPT_DIR/lib.sh"

require_root
load_vps_env
requested_image=${1:-$APP_IMAGE}
case "$requested_image" in
    ghcr.io/gleisonem/starkbank-backend-challenge@sha256:*|\
    docker.io/gemanueldev/starkbank-backend-challenge@sha256:*) ;;
    *) fail "release image must use an approved repository pinned by sha256 digest" ;;
esac
requested_digest=${requested_image##*@sha256:}
[ "${#requested_digest}" -eq 64 ] || fail "release image digest must contain 64 characters"
case "$requested_digest" in
    *[!0-9a-f]*) fail "release image digest must be lowercase hexadecimal" ;;
esac

install -d -m 0750 -o root -g root "$STATE_DIR"
install -d -m 0750 -o 10001 -g 10001 "$STATE_DIR/logs"
old_image=$APP_IMAGE
temporary_env=$(mktemp "${VPS_ENV_FILE}.XXXXXX")
trap 'rm -f "$temporary_env"' EXIT HUP INT TERM
awk -v image="$requested_image" '
    BEGIN { replaced = 0 }
    /^APP_IMAGE=/ { print "APP_IMAGE=" image; replaced = 1; next }
    { print }
    END { if (!replaced) print "APP_IMAGE=" image }
' "$VPS_ENV_FILE" > "$temporary_env"
install -m 0644 -o root -g root "$temporary_env" "$VPS_ENV_FILE"

rollback() {
    rollback_env=$(mktemp "${VPS_ENV_FILE}.rollback.XXXXXX")
    awk -v image="$old_image" '
        /^APP_IMAGE=/ { print "APP_IMAGE=" image; next }
        { print }
    ' "$VPS_ENV_FILE" > "$rollback_env"
    install -m 0644 -o root -g root "$rollback_env" "$VPS_ENV_FILE"
    rm -f "$rollback_env"
    compose up --detach --wait --wait-timeout 120 postgres migrate api worker scheduler caddy || true
    compose up --detach --force-recreate --no-deps caddy || true
}

"$SCRIPT_DIR/config-check.sh" >/dev/null
"$SCRIPT_DIR/validate-secrets.sh" >/dev/null
if ! docker image inspect "$requested_image" >/dev/null 2>&1 && ! compose pull; then
    rollback
    fail "image pull failed; previous APP_IMAGE was restored"
fi
if ! compose up --detach --wait --wait-timeout 180 postgres migrate api worker scheduler caddy; then
    rollback
    fail "deployment failed; previous APP_IMAGE was restored"
fi
if ! compose up --detach --force-recreate --no-deps caddy; then
    rollback
    fail "proxy reload failed; previous APP_IMAGE was restored"
fi
if ! "$SCRIPT_DIR/health.sh"; then
    rollback
    fail "health check failed; previous APP_IMAGE was restored"
fi

printf '%s\n' "$old_image" > "$STATE_DIR/previous-image"
chmod 0600 "$STATE_DIR/previous-image"
printf 'Released %s\n' "$requested_image"
