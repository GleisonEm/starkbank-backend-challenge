# Run locally

The local setup is an independent copy of the application. It uses `compose.yaml`, its own
`.env`, and its own `secrets/` directory. It does not read VPS files or connect to the VPS
database.

## What you need

- Docker with Compose v2;
- GNU Make and `curl`;
- a Stark Bank key only if you want to make real Sandbox calls.

If you are using Docker:

```bash
docker version
docker compose version
```

## Run the tests only

The automated tests do not require Stark Bank credentials:

```bash
git clone https://github.com/GleisonEm/starkbank-backend-challenge.git
cd starkbank-backend-challenge
make test
make check
```

These commands use a temporary PostgreSQL database and a fake provider. They do not load `.env`,
use a private key, or create an Invoice, Webhook, or Transfer.

## Start the local application

Create the local configuration and a key pair:

```bash
make env-init
make build
make starkbank-keygen
```

The command creates `secrets/private-key.pem` and `secrets/public-key.pem`. The private key stays
on the machine and must never be committed. The public key only needs to be uploaded to Stark Bank
if you are creating your own Sandbox Project.

Then edit `.env`:

- replace `POSTGRES_PASSWORD=change-local-password`;
- provide numeric values for `STARKBANK_PROJECT_ID` and `STARKBANK_WORKSPACE_ID`;
- keep `STARKBANK_SANDBOX_LIVE_ENABLED=false`;
- leave `PUBLIC_BASE_URL` empty until you have a tunnel.

If you only want to start the application without calling Sandbox, the IDs can be numeric test
values. For real provider calls, use the Project and Workspace from your own Sandbox account.

```bash
make validate-env
make up
make health
make ps
```

The API is available at `http://127.0.0.1:8787` and PostgreSQL at `127.0.0.1:55432`. If either
port is already in use, change `API_PORT` or `POSTGRES_PORT` in `.env`.

## Test the Sandbox

When you are ready to test the real integration, run `make starkbank-keygen`, upload only
`secrets/public-key.pem` to your Sandbox Project, and set the correct Project and Workspace IDs.
Do not put the private key in GitHub, the README, or a Docker image.

To receive webhooks on your computer, use the Quick Tunnel:

```bash
make tunnel
make tunnel-url
```

Put the printed HTTPS URL in `PUBLIC_BASE_URL`, enable Sandbox mode, and follow
[`SANDBOX.md`](SANDBOX.md):

```bash
make sandbox-check
make webhook-setup
make smoke-batch COUNT=8 CONFIRM_SANDBOX=yes
```

The Quick Tunnel runs inside Compose and does not require a Cloudflare account or token. It is
temporary and is not used on the VPS. Ngrok is an alternative, but normally requires an account
and authtoken.

## Sharing a Sandbox workspace safely

Stark Bank delivers every event to **all webhooks registered in the workspace** where the
operation happened, and it retries failed deliveries. Two applications (for example, this
local clone and the VPS deployment) can therefore run at the same time against the **same
Project and Workspace** without interfering: each environment only queues transfers for
invoices it created (trial drafts and smoke invoices are registered in its own database), and
credits for unknown invoices are stored as `invoice_unknown` audit records instead of
transfers. See [`SANDBOX.md`](SANDBOX.md) "Shared workspace" for the exact behavior.

Keep in mind:

- Each environment still needs its own database; never point two clones at the same
  PostgreSQL.
- `PUBLIC_BASE_URL` and the registered webhook are per environment — the Quick Tunnel URL
  changes every session, so re-run `make tunnel-url` and `make webhook-setup` when it changes.
- If a local test created invoices and you want best-effort cleanup of the workspace's event
  history, use `provider cleanup-events` from [`SANDBOX.md`](SANDBOX.md). It is optional
  maintenance, not isolation or retry cancellation; unknown-credit filtering and local
  ownership records are the concurrency controls.
- A separate Project/Workspace per environment is still the cleanest setup when you have
  permission to create one; sharing is only needed when it is not available (for example, the
  Sandbox account may not allow creating extra workspaces).

## Logs and shutdown

```bash
make logs
make logs-webhook
make down
```

`make down` stops the containers and keeps the local database and logs. To delete this clone's
volumes, run `make reset CONFIRM_RESET=yes`. This cannot affect the VPS.
