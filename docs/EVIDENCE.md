# Submission evidence

This file is intentionally redacted. It records operational evidence without financial IDs,
webhook payloads, signatures, payer data, private keys, or provider credentials.

## Release

- Source commit: `<full commit SHA>`
- Published image: `ghcr.io/gleisonem/starkbank-backend-challenge@sha256:<digest>`
- Image provenance source SHA: `<full commit SHA>`
- Deployment time (UTC): `<timestamp>`
- Public readiness result: `HTTP 200 /health/ready`
- Live mode at deployment: `false`

## Safety checks

- [ ] Workspace ID is configured as a non-secret Sandbox setting.
- [ ] Live mode disabled before deployment.
- [ ] Trial start rejected while disabled without creating a database trial.
- [ ] Worker did not claim a transfer while disabled.
- [ ] Signed foreign-workspace event returned `ignored_workspace` and created no record.
- [ ] Legacy domain webhook removed and the public endpoint retains only the `invoice` subscription.

## Accelerated Sandbox validation

- Reference: `<stable redacted reference>`
- Started (UTC): `<timestamp>`
- Requested invoices: `8`
- First run created: `<count>`
- Repeated run reused: `<count>`
- Credited invoices observed: `<count>`
- Successful net transfers observed: `<count>`
- Duplicate event/invoice/tag/external-ID check: `<pass/fail>`

## Official 24-hour trial

- Started (UTC): `<timestamp>`
- Schedule: 8 batches at 0, 3, 6, 9, 12, 15, 18 and 21 hours.
- Target per batch: 8–12 invoices.
- Status at submission: `active` or `<final status>`
- Invoices after completion: `<64–96>`
- Webhook outcomes after completion: `<redacted aggregate>`
- Transfers after completion: `<redacted aggregate>`
