# Review API

The review API is a read-only view of the trial. It is useful when looking at the running
instance from Postman, Insomnia or `curl`, while the CLI remains the safer interface for commands
that change state.

## Access

Set these two values in the client collection:

```dotenv
base_url=http://127.0.0.1:8787
review_token=the-private-review-token
```

For the VPS, use the public base URL supplied for the evaluation. The token is delivered
privately and is not stored in this repository. Do not send the VPS runtime env file: it contains
database and provider credentials that are not needed by a reviewer.

Import `api-clients/starkbank-trial.postman_collection.json` into Postman or Insomnia. The file is
a Postman v2.1 collection and contains the main list, detail and status filters.

## Endpoints

All review endpoints require `Authorization: Bearer <token>`:

```text
GET /api/v1/review/overview
GET /api/v1/review/configuration
GET /api/v1/review/schedule
GET /api/v1/review/provider/webhooks
GET /api/v1/review/trials
GET /api/v1/review/trials/<trial_id>
GET /api/v1/review/batches
GET /api/v1/review/batches/<batch_id>
GET /api/v1/review/invoices
GET /api/v1/review/invoices/<draft_id>
GET /api/v1/review/transfers
GET /api/v1/review/transfers/<job_id>
GET /api/v1/review/webhook-events
GET /api/v1/review/webhook-events/<event_id>
```

List responses use `data` and `pagination`. The cursor is opaque:

```json
{
  "data": [],
  "pagination": {
    "limit": 25,
    "total": 0,
    "next_cursor": null
  }
}
```

Useful filters include `trial_id`, `status`, `provider_status`, `credit_status`, `from_at`,
`to_at`, `limit` and `cursor`. Status filters may be repeated, for example
`?status=failed&status=unknown`.

The API deliberately omits payer identity, tax IDs, bank account data, raw webhook bodies and
signatures. Amounts are integer cents. Transfer `dispatch_status` describes this application's
durable job; `provider_status` describes the latest status received from Stark Bank.

## Local commands

```bash
make health
curl -H "Authorization: Bearer $REVIEW_API_TOKEN" \
  http://127.0.0.1:8787/api/v1/review/overview
```

The endpoint is read-only. Trial start, smoke calls, webhook setup, worker operations and live-mode
changes remain explicit CLI/Make commands.
