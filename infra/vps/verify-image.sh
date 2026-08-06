#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=infra/vps/lib.sh
. "$SCRIPT_DIR/lib.sh"

require_root
requested_image=${1:-}
requested_revision=${2:-}

case "$requested_image" in
    ghcr.io/gleisonem/starkbank-backend-challenge@sha256:*|\
    docker.io/gemanueldev/starkbank-backend-challenge@sha256:*) ;;
    *) fail "image must use an approved repository pinned by sha256 digest" ;;
esac
requested_digest=${requested_image##*@sha256:}
[ "${#requested_digest}" -eq 64 ] || fail "image digest must contain 64 characters"
case "$requested_digest" in
    *[!0-9a-f]*) fail "image digest must be lowercase hexadecimal" ;;
esac

[ "${#requested_revision}" -eq 40 ] || fail "source revision must contain 40 characters"
case "$requested_revision" in
    *[!0-9a-f]*) fail "source revision must be lowercase hexadecimal" ;;
esac

[ "$(git -C "$REPOSITORY_ROOT" rev-parse HEAD)" = "$requested_revision" ] || \
    fail "checked-out VPS source does not match requested revision"

if ! docker image inspect "$requested_image" >/dev/null 2>&1; then
    docker pull "$requested_image" >/dev/null
fi
image_revision=$(docker image inspect \
    --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
    "$requested_image")
[ "$image_revision" = "$requested_revision" ] || \
    fail "image revision label does not match requested source revision"

printf 'Image digest and source revision match.\n'
