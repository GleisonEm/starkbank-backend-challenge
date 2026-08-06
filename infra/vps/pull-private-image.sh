#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=infra/vps/lib.sh
. "$SCRIPT_DIR/lib.sh"

require_root
requested_image=${1:-}
registry_user=${2:-}

case "$requested_image" in
    ghcr.io/gleisonem/starkbank-backend-challenge@sha256:*) ;;
    *) fail "image must be the repository GHCR image pinned by sha256 digest" ;;
esac
requested_digest=${requested_image##*@sha256:}
[ "${#requested_digest}" -eq 64 ] || fail "image digest must contain 64 characters"
case "$requested_digest" in
    *[!0-9a-f]*) fail "image digest must be lowercase hexadecimal" ;;
esac

[ -n "$registry_user" ] || fail "GitHub registry user is required"
case "$registry_user" in
    *[!A-Za-z0-9-]*) fail "GitHub registry user contains invalid characters" ;;
esac

registry_config=$(mktemp -d /run/starkbank-trial-registry.XXXXXX)
cleanup() {
    DOCKER_CONFIG=$registry_config docker logout ghcr.io >/dev/null 2>&1 || true
    rm -rf "$registry_config"
}
trap cleanup EXIT HUP INT TERM

export DOCKER_CONFIG="$registry_config"
docker login ghcr.io --username "$registry_user" --password-stdin >/dev/null
docker pull "$requested_image" >/dev/null
printf 'Pulled immutable release image; registry credentials removed.\n'
