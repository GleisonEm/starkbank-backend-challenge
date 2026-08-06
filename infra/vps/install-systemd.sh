#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=infra/vps/lib.sh
. "$SCRIPT_DIR/lib.sh"

require_root
install -d -m 0750 -o root -g root /etc/starkbank-trial
install -d -m 0700 -o root -g root "$SECRETS_ROOT"
install -d -m 0750 -o 10001 -g 10001 "$STATE_DIR/logs"
install -d -m 0700 -o root -g root "$BACKUP_DIR"
install -m 0644 "$SCRIPT_DIR/systemd/starkbank-trial.service" /etc/systemd/system/
install -m 0644 "$SCRIPT_DIR/systemd/starkbank-trial-backup.service" /etc/systemd/system/
install -m 0644 "$SCRIPT_DIR/systemd/starkbank-trial-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable starkbank-trial.service starkbank-trial-backup.timer
printf 'Systemd units installed. Configure vps.env and deploy GitHub secrets before starting.\n'
