# Submission evidence

This file is intentionally redacted. It records operational evidence without financial IDs,
webhook payloads, signatures, payer data, private keys, or provider credentials.

## Release

- Source commit: `525ce075c2bb39cef8da97b621b300ed3312a84f`
- Published image: `docker.io/gemanueldev/starkbank-backend-challenge@sha256:8188b209893132cf43c6e5e0c3cf6bde9ff297e09df0ec40fd07e8258bdb8470`
- Image provenance source SHA: `525ce075c2bb39cef8da97b621b300ed3312a84f`
- Deployment time (UTC): `2026-08-06T21:44:58Z`
- Public readiness result: `HTTP 200 /health/ready`
- Live mode at deployment: `false`
- Runtime log audit: `0 error/critical events; 0 malformed JSONL records`

## Safety checks

- [x] Workspace ID is configured as a non-secret Sandbox setting.
- [x] Live mode disabled before deployment.
- [x] Trial start rejected while disabled without creating a database trial.
- [x] Worker did not claim a transfer while disabled, covered by the automated kill-switch tests.
- [x] Signed foreign-workspace event returned `ignored_workspace` and created no record in the
  automated webhook test.
- [x] Legacy domain webhook removed and the public endpoint retains only the `invoice`
  subscription.
- [x] Unsigned request to the public webhook returned HTTP 400.

## Accelerated Sandbox validation

- Reference: `submission-20260806`
- Started (UTC): `2026-08-06T21:47:26Z`
- Requested invoices: `8`
- First run created: `8`
- Repeated run reused: `8 of 8`
- Credited invoices observed: `7`
- Successful net transfers observed: `7`
- Transfer attempts: `exactly 1 per successful job`
- Net amount check: `received amount minus fee for all 7 jobs`
- Duplicate event/invoice/tag/external-ID check: `pass; 7 of 7 unique in every dimension`

## Official 24-hour trial

- Started (UTC): `2026-08-06T21:54:42Z`
- Schedule: 8 batches at 0, 3, 6, 9, 12, 15, 18 and 21 hours.
- Target per batch: 8–12 invoices.
- Status at submission: `active`; first batch completed with 11 of 11 Invoices created.
- Remaining batches at submission: `7 scheduled`.
- Total scheduled Invoices: `80`.
- Expected completion (UTC): `2026-08-07T21:54:42Z`.
- Final webhook and Transfer aggregates will be appended after the 24-hour run completes.
