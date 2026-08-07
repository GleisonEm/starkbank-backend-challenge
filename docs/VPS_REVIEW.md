# VPS reviewer guide

Connect using your own SSH key:

```bash
ssh reviewer@159.223.160.99
cd /opt/starkbank-trial
```

Read-only operational inspection:

```bash
git status --short
git rev-parse HEAD
sudo make vps-status
sudo make vps-health
sudo make vps-trial-status
sudo make vps-live-status
sudo docker compose --env-file /etc/starkbank-trial/vps.env \
  -f compose.vps.yaml -p starkbank-trial-vps logs --tail=200 api worker scheduler
sudo journalctl -u starkbank-trial.service --since today

# Read-only HTTP review API: configure the private token in your client first.
curl -H 'Authorization: Bearer <review-token>' \
  https://159.223.160.99/api/v1/review/overview
```

With the owner's approval, the deployment path can also be exercised:

```bash
sudo make vps-release
```

This is a mutating command. With no `RELEASE_IMAGE`, it redeploys the already-configured immutable
digest and runs migrations plus internal and public health checks. It does not build source on the
VPS.

The Sandbox validation controls are intentionally explicit:

```bash
sudo make vps-live-enable CONFIRM_SANDBOX=yes
sudo make vps-smoke-batch COUNT=8 CONFIRM_SANDBOX=yes
sudo make vps-trial-start CONFIRM_SANDBOX=yes
sudo make vps-live-disable
```

Please do not print, copy or modify:

- `/etc/starkbank-trial/secrets/*`;
- Docker secret mounts;
- payer data or raw webhook payloads.

The reviewer account has temporary passwordless full sudo. Therefore it can technically access
the containers, PostgreSQL volume, Stark Bank key and database password. This is intentional
for transparent evaluation, not a claim of isolation. The VPS contains only this Sandbox project,
and all access is revoked after the review.

To run an independent copy without touching this deployment, clone the public repository on your
own computer and follow [`LOCAL.md`](LOCAL.md).
