# Research record

Research performed on 2026-07-31. These references record the contracts used
during implementation; provider behavior should still be checked against the
current documentation before operating outside Sandbox.

## Stark Bank contract

- [API documentation](https://docs.starkbank.com/api) confirms official
  Python SDK usage, Project/Sandbox configuration, and integer-cent resources.
- [Pix Invoice guide](https://docs.starkbank.com/get-started/pix-invoice)
  confirms `invoice` subscription, the `credited` log, Invoice `amount` and
  `fee`, and the webhook payload shape.
- [Webhook guide](https://docs.starkbank.com/get-started/webhook) confirms
  `Digital-Signature`, SDK `event.parse`, quick HTTP 200, duplicate and
  out-of-order delivery, and reacting to `credited` for an account-funded
  transfer.
- [Transfer guide](https://docs.starkbank.com/get-started/transfer) confirms
  integer-cent amounts, `account_type=payment`, and unique `external_id`.
- [Sandbox guide](https://docs.starkbank.com/sandbox) confirms isolated
  credentials, public HTTPS webhook URLs, and automatic Sandbox payments.

## Flask and security references

- [Flask application factories](https://flask.palletsprojects.com/en/stable/patterns/appfactories/),
  [testing](https://flask.palletsprojects.com/en/stable/testing/), and
  [deployment](https://flask.palletsprojects.com/en/stable/deploying/) support
  the HTTP adapter guidance.
- [OWASP Secrets Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
  informed the separation between source-controlled configuration, GitHub
  Environment secrets and root-only runtime files on the VPS.
