# Stark Bank Sandbox runbook

All provider-changing commands require an explicit local or VPS opt-in. Builds, tests and normal
startup never call Stark Bank.

## Configure the local callback

Start the application and Quick Tunnel:

```bash
make up
make tunnel
make tunnel-url
```

Set the printed origin in `.env` without a trailing path:

```dotenv
PUBLIC_BASE_URL=https://random-name.trycloudflare.com
STARKBANK_SANDBOX_LIVE_ENABLED=true
```

Validate and idempotently create the Invoice webhook:

```bash
make sandbox-check
make webhook-setup
make webhook-list
make webhook-cleanup CONFIRM_SANDBOX=yes
```

The registered endpoint must be exactly:

```text
${PUBLIC_BASE_URL}/webhooks/starkbank
```

Delete stale dashboard endpoints that point to an old Quick Tunnel or only to the origin root.
Unsigned health probes or hand-written POST requests are expected to log `missing_signature` or
`invalid_signature_or_payload`; a real Stark Bank event must include `Digital-Signature` and be
validated from its raw bytes.

## Shared workspace: each environment only processes what it created

Events are delivered to every webhook of the workspace, so two environments using the same
Project/Workspace receive the same events. To keep them isolated without stopping either one,
each environment only queues transfers for invoices **it** created:

- Trial invoices are registered in the local database (`invoice_drafts`) before they are
  issued; the webhook looks up the invoice id before queueing a transfer.
- `smoke-invoice` and `smoke-batch` register the invoices they create in the local database.
- A credit event for an invoice that this environment does not know is persisted with the
  outcome `invoice_unknown` and never queues a transfer — it becomes part of the audit trail
  only.

This means the VPS and a local clone can run at the same time against the same Sandbox
workspace without double transfers: each worker only ever transfers invoices its own database
created.

## Clean up after local experiments

Stark Bank retries failed webhook deliveries. If a local test created invoices or transfers in
a workspace that another environment also uses, you can remove the notification events of that
test window from the provider — this prevents a stopped environment from receiving them late.

```bash
make webhook-list
docker compose --env-file .env -f compose.yaml -p starkbank-trial-local run --rm scheduler \
    starkbank-trial provider cleanup-events \
    --after "2026-08-07T10:00:00+00:00" --confirm-sandbox
```

`cleanup-events` deletes the notification events created inside the `--after` (required)
to `--before` (optional, default: now) window and reports the deleted and failed ids as
machine-readable JSON. It only touches the workspace of the configured credentials, requires
`STARKBANK_SANDBOX_LIVE_ENABLED=true` and `--confirm-sandbox`, and never deletes invoices or
transfers — it only removes undelivered event notifications so they are not retried later.
This is a maintenance command; the challenge flow does not need it.

## Smoke Invoice

Create one deterministic Invoice before the 24-hour run:

```bash
make smoke-invoice CONFIRM_SANDBOX=yes
make smoke-batch COUNT=8 CONFIRM_SANDBOX=yes
```

Retrying the command reuses the Invoice found by its stable tag instead of creating another one.
Observe:

```bash
make logs-webhook
make trial-status
```

The Sandbox emulator may credit some invoices and leave others open. Invoice amount alone is not
proof of a defect. For each accepted credit event, the database must contain one transfer job for
`received_amount - fee`; the worker reconciles by stable external ID before creation.

## Start the 24-hour challenge

Only after the webhook and accelerated smoke flow are healthy:

```bash
make trial-start CONFIRM_SANDBOX=yes
make trial-status
```

The trial has eight persisted three-hour slots. Each due slot creates a cryptographically random
count from 8 through 12. Re-running `trial start` while one is active is rejected. Supercronic
checks every minute, while database state decides whether any batch is due.

Useful observation commands:

```bash
make ps
make logs
make logs-webhook
make trial-status
make webhook-list
```

On the VPS, use the equivalent `vps-*` status and log commands from
[`OPERATIONS.md`](OPERATIONS.md). The VPS keeps live Sandbox opt-in in
`/etc/starkbank-trial/vps.env`; do not enable it until the deployment and webhook are verified.
