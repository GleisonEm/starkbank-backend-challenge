# VPS reviewer guide

Connect using your own SSH key:

```bash
ssh reviewer@mac3.gemanuel.site
cd /opt/starkbank-trial
```

Read-only operational inspection:

```bash
git status --short
git rev-parse HEAD
sudo make vps-status
sudo make vps-health
sudo make vps-trial-status
sudo docker compose --env-file /etc/starkbank-trial/vps.env \
  -f compose.vps.yaml -p starkbank-trial-vps logs --tail=200 api worker scheduler
sudo journalctl -u starkbank-trial.service --since today
```

With the owner's approval, the deployment path can also be exercised:

```bash
sudo make vps-release
```

This is a mutating command. With no `RELEASE_IMAGE`, it redeploys the already-configured immutable
digest and runs migrations plus internal and public health checks. It does not build source on the
VPS.

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
