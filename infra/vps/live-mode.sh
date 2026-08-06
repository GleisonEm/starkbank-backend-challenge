#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=infra/vps/lib.sh
. "$SCRIPT_DIR/lib.sh"

require_root
action=${1:-}
case "$action" in
    enable) value=true ;;
    disable) value=false ;;
    *) fail "usage: live-mode.sh enable|disable" ;;
esac

"$SCRIPT_DIR/config-check.sh" >/dev/null
temporary_env=$(mktemp "${VPS_ENV_FILE}.live.XXXXXX")
previous_env=$(mktemp "${VPS_ENV_FILE}.previous.XXXXXX")
cleanup() {
    rm -f "$temporary_env" "$previous_env"
}
trap cleanup EXIT HUP INT TERM
cp -p "$VPS_ENV_FILE" "$previous_env"
awk -v value="$value" '
    BEGIN { replaced = 0 }
    /^STARKBANK_SANDBOX_LIVE_ENABLED=/ {
        print "STARKBANK_SANDBOX_LIVE_ENABLED=" value
        replaced = 1
        next
    }
    { print }
    END {
        if (!replaced) print "STARKBANK_SANDBOX_LIVE_ENABLED=" value
    }
' "$VPS_ENV_FILE" > "$temporary_env"
install -m 0644 -o root -g root "$temporary_env" "$VPS_ENV_FILE"

if ! compose up --detach --force-recreate --no-deps api worker scheduler; then
    install -m 0644 -o root -g root "$previous_env" "$VPS_ENV_FILE"
    compose up --detach --force-recreate --no-deps api worker scheduler >/dev/null 2>&1 || true
    fail "could not recreate application services; previous live mode restored"
fi

printf 'Sandbox live mode is now %s.\n' "$value"
