#!/bin/sh
set -eu

REPOSITORY_ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
SECRETS_DIR="$REPOSITORY_ROOT/secrets"
PRIVATE_KEY="$SECRETS_DIR/private-key.pem"
PUBLIC_KEY="$SECRETS_DIR/public-key.pem"
IMAGE=${APP_IMAGE:-starkbank-trial:local}

[ -e "$PRIVATE_KEY" ] && {
    printf 'refusing to overwrite %s\n' "$PRIVATE_KEY" >&2
    exit 1
}
[ -e "$PUBLIC_KEY" ] && {
    printf 'refusing to overwrite %s\n' "$PUBLIC_KEY" >&2
    exit 1
}

mkdir -p "$SECRETS_DIR"
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build --target runtime --tag "$IMAGE" "$REPOSITORY_ROOT"
docker run --rm \
    --user "$(id -u):$(id -g)" \
    --volume "$SECRETS_DIR:/output" \
    --entrypoint python \
    "$IMAGE" \
    -c 'from pathlib import Path; import starkbank; private, public = starkbank.key.create(); Path("/output/private-key.pem").write_text(private + "\n", encoding="utf-8"); Path("/output/public-key.pem").write_text(public + "\n", encoding="utf-8")'
chmod 600 "$PRIVATE_KEY"
printf 'Created secrets/private-key.pem and secrets/public-key.pem. Upload only the public key.\n'
