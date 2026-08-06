# Local environment

This path is a disposable clone on the evaluator's own computer. It cannot read
`/etc/starkbank-trial`, does not use the VPS Compose project and never connects to the VPS
database.

## Requirements

- Docker Engine or Docker Desktop with Compose v2
- GNU Make and `curl`
- A Stark Bank Sandbox Project and its private key only for real provider operations

On macOS with Colima:

```bash
colima start --cpu 2 --memory 4 --disk 30
docker version
docker compose version
```

## Bootstrap

```bash
git clone https://github.com/GleisonEm/starkbank-backend-challenge.git
cd starkbank-backend-challenge
make env-init
mkdir -p secrets
cp /path/to/private-key.pem secrets/private-key.pem
chmod 600 secrets/private-key.pem
```

Alternatively, generate a key pair after building the image:

```bash
make build
make keygen
```

Upload only `secrets/public-key.pem` when creating the Sandbox Project. Never upload or commit
`secrets/private-key.pem`.

Edit `.env`:

- replace `POSTGRES_PASSWORD=change-local-password`;
- set the numeric `STARKBANK_PROJECT_ID`;
- leave `PUBLIC_BASE_URL` empty until a tunnel exists;
- leave `STARKBANK_SANDBOX_LIVE_ENABLED=false` during ordinary startup.

Then run:

```bash
make validate-env
make build
make up
make health
make ps
```

The API is available only at `http://127.0.0.1:8787`. PostgreSQL is available only at
`127.0.0.1:55432`. Change `API_PORT` or `POSTGRES_PORT` in `.env` if either port is occupied.

## Tests and logs

```bash
make test
make check
make logs
make logs-webhook
```

`make test` and `make check` use `compose.test.yaml`, an isolated temporary PostgreSQL service
and fake provider boundaries. They do not load `.env`, mount a private key or call Sandbox.

Application JSONL logs persist in the `starkbank-trial-local_app_logs` volume. Container stdout
also uses structured JSON. `make logs-webhook` follows persisted webhook records specifically.

## Quick Tunnel

With the application healthy:

```bash
make tunnel
make tunnel-url
```

The second command waits up to 30 seconds and prints the generated `https://*.trycloudflare.com`
origin. Put it in `PUBLIC_BASE_URL`, set `STARKBANK_SANDBOX_LIVE_ENABLED=true`, and follow
[`SANDBOX.md`](SANDBOX.md).

Quick Tunnel runs inside Compose. It requires neither a Cloudflare account nor a local binary.
It is explicitly temporary and has no SLA. A `cloudflared` token is not accepted by this Compose
file. Stop only the tunnel with `make tunnel-down`. See the
[official Cloudflare Quick Tunnel documentation](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).

Ngrok can expose `http://127.0.0.1:8787`, but its current agent flow requires an account and
authtoken, so it is not a project dependency. See the [ngrok agent CLI documentation](https://ngrok.com/docs/agent/cli/).

## Shutdown

```bash
make down
```

This keeps database and log volumes. To delete only this local project's volumes:

```bash
make reset CONFIRM_RESET=yes
```

The command cannot address the VPS Compose project.
