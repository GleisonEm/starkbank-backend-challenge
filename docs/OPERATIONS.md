# Operations

All VPS commands must run as root from `/opt/starkbank-trial`. They use only
`/etc/starkbank-trial/vps.env`, `/etc/starkbank-trial/secrets/*` and `compose.vps.yaml`; `.env` is
never consulted.

## Status and logs

```bash
sudo make vps-status
sudo make vps-health
sudo make vps-trial-status
sudo make vps-logs
sudo journalctl -u starkbank-trial.service --since today
```

Application JSONL logs persist at `/var/lib/starkbank-trial/logs/starkbank-trial.jsonl`.
Docker's stdout/stderr logs rotate at 10 MB with five files per service. PostgreSQL and Caddy use
named volumes; application logs use a host directory to simplify review and backup.

## Manual immutable release

Obtain the digest from the successful CI publish summary or GHCR, then:

```bash
sudo make vps-verify-image \
  RELEASE_IMAGE=ghcr.io/gleisonem/starkbank-backend-challenge@sha256:<digest> \
  RELEASE_SOURCE_SHA=<full-40-character-commit>
sudo make vps-release \
  RELEASE_IMAGE=ghcr.io/gleisonem/starkbank-backend-challenge@sha256:<digest>
```

The release flow validates the digest and persistent runtime secret files, saves the old image,
runs migrations, starts services and checks both internal and public readiness. It pulls a missing
image, while the GitHub workflow preloads private GHCR images with ephemeral authentication. Pull,
startup or health failure restores the previous `APP_IMAGE` and attempts to bring it back up.
Caddy is force-recreated so source-only proxy configuration changes are loaded; public readiness
retries are bounded while an initial ACME certificate is issued.

The `deploy-vps` GitHub workflow performs this command over SSH only after manual approval of the
`production` Environment. Configure required reviewers, prevent self-review, restrict deployment
to `main`, and add only these environment secrets. GitHub documents these gates under
[Deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments).

- `VPS_SSH_KEY`
- `STARKBANK_PROJECT_ID`
- `STARKBANK_PRIVATE_KEY_PEM`
- `POSTGRES_PASSWORD`

Configure these non-secret Environment variables separately:

- `GHCR_IMAGE`
- `PUBLIC_BASE_URL`
- `PUBLIC_HOST`
- `VPS_HOST`
- `VPS_KNOWN_HOSTS`
- `VPS_USER`

The workflow also requires the full source SHA printed by CI. It checks out that exact commit on
the VPS, streams the workflow's short-lived `GITHUB_TOKEN` to a one-shot private-image pull, and
verifies the image's `org.opencontainers.image.revision` label before installing secrets or
releasing. The registry configuration is removed before the pull command exits. Application
secret values are streamed through SSH standard input and never included in command arguments,
the image or the workflow summary.

To rotate the Stark Bank key or Project ID, update it in the `production` Environment and run an
approved deployment again. The installer validates the complete set before replacing root-only
VPS files. PostgreSQL password rotation additionally requires updating the initialized database
role; the installer rejects a changed password to prevent an accidental lockout.

## Rollback

```bash
sudo make vps-rollback
sudo make vps-health
```

The previous immutable reference is stored in `/var/lib/starkbank-trial/previous-image`. A
successful rollback records the image it replaced, so one-step forward recovery remains possible.

## Backup and restore

Create a PostgreSQL custom-format backup:

```bash
sudo make vps-backup
sudo ls -lh /var/backups/starkbank-trial
```

Backups are mode `0600`. The included systemd timer runs nightly at 03:15 UTC with jitter. Copy
important backups off-host using an encrypted channel; local files alone do not protect against
VPS loss.

Restore is intentionally guarded and accepts only an absolute `.dump` path under the backup
directory:

```bash
sudo make vps-restore \
  BACKUP=/var/backups/starkbank-trial/starkbank-trial-YYYYMMDDTHHMMSSZ.dump \
  CONFIRM_RESTORE=yes
```

The command stops API, worker and scheduler, restores with `--clean --if-exists`, reapplies
migrations, starts services and runs readiness checks. Take a fresh backup before any restore.

## Shutdown and boot

```bash
sudo make vps-down
sudo make vps-up
sudo systemctl restart starkbank-trial.service
```

`vps-down` removes containers and networks but preserves PostgreSQL and Caddy named volumes plus
host application logs.
