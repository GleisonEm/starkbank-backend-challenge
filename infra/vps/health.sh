#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=infra/vps/lib.sh
. "$SCRIPT_DIR/lib.sh"

require_root
load_vps_env
compose exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=5)"
curl --fail --silent --show-error --max-time 10 "$PUBLIC_BASE_URL/health/ready"
printf '\nVPS internal and public readiness checks passed.\n'
