# VPS installation

The official deployment is intentionally small: one dedicated Ubuntu VPS, Docker Compose,
PostgreSQL, API, worker, scheduler and Caddy. A 1 vCPU / 2 GB instance is sufficient for the trial
because it does not build images on-host; 2 vCPU / 2 GB is more comfortable for maintenance.

The VPS does not use `.env`, `compose.yaml`, local volumes or Cloudflare Tunnel.

## 1. Base host

Install Docker Engine with Compose v2 from Docker's official Ubuntu repository, then install the
remaining host tools:

```bash
sudo apt-get update
sudo apt-get install --yes ca-certificates curl git make openssl
docker version
docker compose version
```

Use the [official Docker Engine installation guide](https://docs.docker.com/engine/install/ubuntu/)
instead of an unverified convenience script.

Allow inbound SSH, HTTP and HTTPS only. With UFW, verify the SSH port before enabling it:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw enable
sudo ufw status verbose
```

Point the `A` record for `mac3.gemanuel.site` directly to the VPS IPv4 address. If IPv6 is not
configured, remove any stale `AAAA` record. Ports 80 and 443 must reach Caddy for ACME and HTTPS.

## 2. Repository

```bash
sudo git clone https://github.com/GleisonEm/starkbank-backend-challenge.git /opt/starkbank-trial
cd /opt/starkbank-trial
```

Keep this checkout root-owned and free of manual edits. The deployment workflow fetches `main`,
verifies that the requested commit belongs to it and checks out that exact commit before release.

## 3. Independent configuration

```bash
sudo install -d -m 0750 /etc/starkbank-trial
sudo cp infra/vps/vps.env.example /etc/starkbank-trial/vps.env
sudo chown root:root /etc/starkbank-trial/vps.env
sudo chmod 0644 /etc/starkbank-trial/vps.env
sudoedit /etc/starkbank-trial/vps.env
```

`vps.env` contains only non-secret configuration and the GHCR image pinned as
`ghcr.io/gleisonem/starkbank-backend-challenge@sha256:<64 hex characters>`.

## 4. GitHub production Environment

Create a GitHub Environment named `production`, restrict deployment to `main`, require manual
approval and disable administrator bypass where the account plan supports it. Environment secrets
are available only after its protection rules pass. See
[GitHub deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments).

Add these application secrets:

1. numeric Stark Bank Sandbox Project ID;
2. complete Stark Bank private PEM key;
3. a 24-128 character PostgreSQL password using `A-Z a-z 0-9 . _ ~ -`.

Use these exact names:

```text
STARKBANK_PROJECT_ID
STARKBANK_PRIVATE_KEY_PEM
POSTGRES_PASSWORD
```

Create a dedicated SSH key for the workflow and add its public half to the selected VPS account.
Store the private half as a secret and the verified host key as an Environment variable:

```text
VPS_SSH_KEY
```

Use these Environment variables for non-secret deployment configuration:

```text
GHCR_IMAGE
PUBLIC_BASE_URL
PUBLIC_HOST
VPS_HOST
VPS_KNOWN_HOSTS
VPS_USER
```

The SSH account must be allowed to run the root-owned Git synchronization, secret installer and
`make vps-release` commands used by `.github/workflows/deploy.yml`. A dedicated account and key are
preferred over reusing personal SSH credentials.

The workflow packages the three application secrets in its ephemeral runner and streams them to
`install-secrets.sh` over SSH. The script validates the numeric Project ID, cryptographic PEM and
database password before writing a versioned directory under `/etc/starkbank-trial/secrets` with
directory mode `0700`. The environment and PostgreSQL password use root-owned mode `0600`; the
private key uses mode `0400` and container UID/GID `10001`, which is readable only through the
Docker bind mount because the host parent directories remain root-only. An atomic `current`
symlink switches all three values together. The workflow's short-lived `GITHUB_TOKEN` is also
streamed over SSH standard input to authenticate one immutable GHCR pull. Docker uses a temporary
configuration under `/run`, logs out and removes that directory before the command exits. No
GitHub credential is stored on the VPS.

```bash
sudo make vps-config-check
sudo make vps-secrets
sudo ls -ld /etc/starkbank-trial/secrets
```

The first command can run after `vps.env` is configured. The second succeeds only after an approved
GitHub deployment has installed the secrets. Do not print or open those files during review.

## 5. Deploy

The GHCR package may remain private. The approved workflow receives a repository-scoped
`GITHUB_TOKEN` with read-only package permission, uses it for one pull and never persists it on the
VPS. The CI summary prints both the immutable image reference and its full source commit after all
gates pass. Start `deploy-vps`, provide those two values and approve the `production` Environment.

```bash
sudo make vps-pull
sudo make vps-deploy
sudo make vps-status
sudo make vps-health
```

Caddy obtains and renews HTTPS certificates automatically from the domain in `PUBLIC_HOST` and
proxies only to the internal `api:8000`; PostgreSQL and the API publish no host ports. See
[Caddy Automatic HTTPS](https://caddyserver.com/docs/automatic-https).

Install boot and backup units after the first successful deployment:

```bash
sudo ./infra/vps/install-systemd.sh
sudo systemctl start starkbank-trial.service
sudo systemctl start starkbank-trial-backup.timer
systemctl list-timers starkbank-trial-backup.timer
```

## 6. Temporary evaluator account

Ask the evaluator for an SSH public key. Never send them a private key:

```bash
sudo adduser --disabled-password --gecos '' reviewer
sudo install -d -m 0700 -o reviewer -g reviewer /home/reviewer/.ssh
sudoedit /home/reviewer/.ssh/authorized_keys
sudo chown reviewer:reviewer /home/reviewer/.ssh/authorized_keys
sudo chmod 0600 /home/reviewer/.ssh/authorized_keys
printf 'reviewer ALL=(ALL:ALL) NOPASSWD: ALL\n' | sudo tee /etc/sudoers.d/starkbank-reviewer
sudo chmod 0440 /etc/sudoers.d/starkbank-reviewer
sudo visudo -cf /etc/sudoers.d/starkbank-reviewer
```

Full sudo is an explicit review choice and grants access to every project secret. Use a dedicated
VPS and deployment key, then follow the revocation checklist in [`SECURITY.md`](../SECURITY.md).
