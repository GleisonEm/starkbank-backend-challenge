#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=infra/vps/lib.sh
. "$SCRIPT_DIR/lib.sh"

require_root
enabled=${1:-}
case "$enabled" in
    true|false) ;;
    *) fail "REVIEW_API_ENABLED must be true or false" ;;
esac

require_file "$VPS_ENV_FILE"
temporary_env=$(mktemp "${VPS_ENV_FILE}.XXXXXX")
trap 'rm -f "$temporary_env"' EXIT HUP INT TERM
awk -v enabled="$enabled" '
    BEGIN { replaced = 0 }
    /^REVIEW_API_ENABLED=/ {
        print "REVIEW_API_ENABLED=" enabled
        replaced = 1
        next
    }
    { print }
    END { if (!replaced) print "REVIEW_API_ENABLED=" enabled }
' "$VPS_ENV_FILE" > "$temporary_env"
install -m 0644 -o root -g root "$temporary_env" "$VPS_ENV_FILE"
