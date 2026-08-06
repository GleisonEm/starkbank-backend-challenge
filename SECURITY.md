# Security policy and deployment boundary

This repository contains a Sandbox-only assessment. Report suspected vulnerabilities privately
to the repository owner and do not include credentials, webhook payloads, payer information or
proofs that move money in a public issue.

## Secret handling

- `.env`, PEM files and `secrets/*` are ignored by Git and excluded from Docker build context.
- The image contains no key, provider Project ID, PostgreSQL password or deployment credential.
- Local Compose mounts the evaluator-provided key at `/run/secrets/starkbank_private_key`.
- VPS secrets are stored in the protected GitHub `production` Environment and are available only
  to its manually approved deployment job.
- The workflow sends Project ID, key and database password through SSH standard input. The VPS
  validates them before installing root-owned mode `0600` files under
  `/etc/starkbank-trial/secrets`; an atomic symlink activates the complete set and values never
  appear in command arguments or workflow summaries.
- Secrets persist across reboot so the VPS does not retain a GitHub token or depend on GitHub
  availability during startup.
- Provider-changing commands require `STARKBANK_SANDBOX_LIVE_ENABLED=true`; Invoice and trial
  commands additionally require an explicit Make confirmation.

## Runtime hardening

- Application containers run as UID 10001 with a read-only root filesystem, dropped Linux
  capabilities, `no-new-privileges`, PID limits and memory/CPU limits.
- API and PostgreSQL are private on the VPS Compose network. Caddy alone publishes 80/443 and
  manages HTTPS certificates.
- The webhook verifies the signature against raw bytes before parsing or persisting data.
- Logs are structured and persisted but exclude raw bodies, signatures, keys and personal data.
- GHCR releases are deployed by immutable digest. The VPS never builds unreviewed source.

## Temporary reviewer sudo

The `reviewer` account deliberately has passwordless full sudo during evaluation. Full sudo is
equivalent to root and can access:

- all containers and volumes;
- the complete PostgreSQL database;
- the Stark Bank private key;
- the root-only runtime secret files;
- configuration and logs.

This is not sandboxing. The risk is constrained operationally:

- the VPS hosts only this assessment;
- all provider credentials are Sandbox-only;
- the GitHub `production` Environment is restricted to `main` and requires approval;
- the deployment SSH key is dedicated to this assessment;
- no personal, production or unrelated credentials exist on the host;
- sudo commands remain visible in authentication logs and the journal.

## Revocation checklist

Immediately after evaluation:

```bash
sudo rm /etc/sudoers.d/starkbank-reviewer
sudo userdel --remove reviewer
sudo rm -rf /etc/starkbank-trial/secrets
```

Then delete or rotate the three application secrets in the GitHub `production` Environment,
remove the deployment and evaluator SSH public keys, rotate the Stark Bank Sandbox key if the VPS
will remain online, review `journalctl` and SSH auth logs, and take the deployment down if it is no
longer needed.

The deletion commands above are intentionally manual and are not Make targets; revocation should
never be triggered accidentally by application automation.
