import base64
import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from flask.testing import FlaskClient
from sqlalchemy import Engine, create_engine, insert

from starkbank_trial.application.transfers import TransferWorker
from starkbank_trial.application.trials import TrialService
from starkbank_trial.application.webhooks import WebhookService
from starkbank_trial.bootstrap import Services
from starkbank_trial.config import Settings
from starkbank_trial.domain.events import IgnoredEvent, VerifiedEvent
from starkbank_trial.domain.invoices import InvoiceDraft, ProviderInvoice
from starkbank_trial.domain.models import TransferCommand
from starkbank_trial.domain.provider import ProviderError, ProviderTransfer, ProviderTransientError
from starkbank_trial.domain.types import EventId, InvoiceId, TransferId
from starkbank_trial.http import create_app
from starkbank_trial.persistence.review_store import (
    ReviewQuery,
    ReviewStore,
    normalize_row_time,
)
from starkbank_trial.persistence.schema import (
    invoice_batches,
    invoice_drafts,
    metadata,
    transfer_jobs,
    trial_runs,
    webhook_events,
)
from starkbank_trial.persistence.stores import build_stores

if TYPE_CHECKING:
    from sqlalchemy.engine import RowMapping

    from starkbank_trial.application.clock import Clock
    from starkbank_trial.infrastructure.starkbank_client import StarkBankClient


@dataclass(frozen=True, slots=True)
class FixedClock:
    current: datetime

    def now(self) -> datetime:
        return self.current


@dataclass(frozen=True, slots=True)
class FixedCounts:
    def next(self) -> tuple[int, ...]:
        return (8, 8, 8, 8, 8, 8, 8, 8)


@dataclass(slots=True)
class FakeGateway:
    webhooks: tuple[object, ...] = ()
    webhook_error: ProviderError | None = None

    def create_invoice(self, draft: InvoiceDraft) -> ProviderInvoice:
        return ProviderInvoice(InvoiceId(f"invoice-{draft.id}"), draft.tag)

    def find_invoice(self, tag: str) -> ProviderInvoice | None:
        return None

    def ensure_transfer(self, command: TransferCommand) -> ProviderTransfer:
        return ProviderTransfer(TransferId("transfer-1"), command.external_id, "created")

    def find_transfer(self, command: TransferCommand) -> ProviderTransfer | None:
        return None

    def verify_event(self, content: bytes, signature: str) -> VerifiedEvent:
        return IgnoredEvent(EventId("event-1"), "transfer", "created", "workspace-1")

    def list_webhooks(self) -> tuple[object, ...]:
        if self.webhook_error is not None:
            raise self.webhook_error
        return self.webhooks


@dataclass(frozen=True, slots=True)
class ReviewFixture:
    client: FlaskClient
    engine: Engine
    gateway: FakeGateway
    settings: Settings
    services: Services


@pytest.fixture
def review_fixture(tmp_path: Path) -> Iterator[ReviewFixture]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'review.db'}")
    metadata.create_all(engine)
    stores = build_stores(engine)
    gateway = FakeGateway()
    clock: Clock = FixedClock(datetime(2026, 8, 1, 12, tzinfo=UTC))
    services = Services(
        engine=engine,
        stores=stores,
        trial=TrialService(stores, gateway, clock, FixedCounts()),
        webhook=WebhookService(gateway, stores.events, clock),
        worker=TransferWorker(stores.transfers, gateway, clock),
        provider=cast("StarkBankClient", gateway),
    )
    settings = Settings.model_validate(
        {
            "DATABASE_URL": str(engine.url),
            "REVIEW_API_ENABLED": True,
            "REVIEW_API_TOKEN": "review-token-that-is-long-enough-for-tests",
            "REVIEW_API_RATE_LIMIT": "20 per minute;100 per hour",
            "LOG_LEVEL": "info",
        }
    )
    app = create_app(settings, services)
    app.testing = True
    yield ReviewFixture(app.test_client(), engine, gateway, settings, services)
    engine.dispose()


def test_review_api_requires_bearer_token(review_fixture: ReviewFixture) -> None:
    response = review_fixture.client.get("/api/v1/review/overview")

    assert response.status_code == 401
    assert response.get_json() == {"error": "review_authentication_required"}


def test_review_api_returns_overview_without_pii(review_fixture: ReviewFixture) -> None:
    # Given
    headers = {"Authorization": "Bearer review-token-that-is-long-enough-for-tests"}

    # When
    response = review_fixture.client.get("/api/v1/review/overview", headers=headers)

    # Then
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["data"]["trial"] is None
    assert payload["data"]["schedule"]["interval_hours"] == 3
    assert "payer_name" not in json.dumps(payload)
    assert "payer_tax_id" not in json.dumps(payload)


def test_review_api_rejects_invalid_limit(review_fixture: ReviewFixture) -> None:
    headers = {"Authorization": "Bearer review-token-that-is-long-enough-for-tests"}

    response = review_fixture.client.get(
        "/api/v1/review/invoices?limit=101",
        headers=headers,
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid_review_query"}


def test_review_api_lists_details_and_redacts_payer_data(review_fixture: ReviewFixture) -> None:
    # Given
    trial_id = "00000000-0000-0000-0000-000000000001"
    batch_id = "00000000-0000-0000-0000-000000000002"
    draft_id = "00000000-0000-0000-0000-000000000003"
    event_id = "event-review-1"
    job_id = "00000000-0000-0000-0000-000000000004"
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    with review_fixture.engine.begin() as connection:
        connection.execute(
            insert(trial_runs).values(
                id=trial_id,
                status="active",
                started_at=now,
                ends_at=datetime(2026, 8, 2, 12, tzinfo=UTC),
                created_at=now,
                active_marker=1,
            )
        )
        connection.execute(
            insert(invoice_batches).values(
                id=batch_id,
                run_id=trial_id,
                slot_index=0,
                scheduled_at=now,
                target_count=8,
                status="completed",
                attempts=1,
                created_at=now,
            )
        )
        connection.execute(
            insert(invoice_drafts).values(
                id=draft_id,
                batch_id=batch_id,
                ordinal=0,
                payer_name="Payer Must Not Leak",
                payer_tax_id="12345678901",
                amount=10000,
                tag="review-tag",
                status="created",
                provider_invoice_id="invoice-review-1",
                attempts=1,
                reconcile_attempts=0,
                created_at=now,
                updated_at=now,
                next_attempt_at=now,
            )
        )
        connection.execute(
            insert(webhook_events).values(
                id="invoice-review-event",
                subscription="invoice",
                log_type="credited",
                invoice_id="invoice-review-1",
                workspace_id="workspace-1",
                payload_hash="i" * 64,
                outcome="queued",
                received_at=now,
            )
        )
        connection.execute(
            insert(webhook_events).values(
                id=event_id,
                subscription="transfer",
                log_type="canceled",
                transfer_id="provider-transfer-1",
                workspace_id="workspace-1",
                payload_hash="hash-not-exposed",
                outcome="transfer_updated",
                received_at=now,
            )
        )
        connection.execute(
            insert(transfer_jobs).values(
                id=job_id,
                event_id=event_id,
                invoice_id="invoice-review-1",
                amount=10000,
                fee=50,
                net_amount=9950,
                external_id="transfer-review-1",
                tag="transfer-tag",
                status="succeeded",
                attempts=1,
                next_attempt_at=now,
                provider_transfer_id="provider-transfer-1",
                provider_status="canceled",
                provider_log_type="canceled",
                provider_status_updated_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    headers = {"Authorization": "Bearer review-token-that-is-long-enough-for-tests"}

    # When
    invoices = review_fixture.client.get(
        "/api/v1/review/invoices?status=created&credit_status=credited"
        f"&batch_id={batch_id}&provider_invoice_id=invoice-review-1&tag=review-tag"
        "&from_at=2026-08-01T11:00:00%2B00:00&to_at=2026-08-01T13:00:00%2B00:00",
        headers=headers,
    )
    transfers = review_fixture.client.get(
        "/api/v1/review/transfers?provider_status=canceled&dispatch_status=succeeded"
        f"&trial_id={trial_id}&batch_id={batch_id}&invoice_id=invoice-review-1"
        "&provider_transfer_id=provider-transfer-1&external_id=transfer-review-1"
        "&provider_log_type=canceled&from_at=2026-08-01T11:00:00%2B00:00"
        "&to_at=2026-08-01T13:00:00%2B00:00",
        headers=headers,
    )
    detail = review_fixture.client.get(f"/api/v1/review/transfers/{job_id}", headers=headers)
    trials = review_fixture.client.get(
        f"/api/v1/review/trials?trial_id={trial_id}&status=active"
        "&from_at=2026-08-01T11:00:00%2B00:00&to_at=2026-08-01T13:00:00%2B00:00",
        headers=headers,
    )
    trial_detail = review_fixture.client.get(f"/api/v1/review/trials/{trial_id}", headers=headers)
    batches = review_fixture.client.get(
        f"/api/v1/review/batches?trial_id={trial_id}&slot_index=0&status=completed"
        "&from_at=2026-08-01T11:00:00%2B00:00&to_at=2026-08-01T13:00:00%2B00:00",
        headers=headers,
    )
    batch_detail = review_fixture.client.get(f"/api/v1/review/batches/{batch_id}", headers=headers)
    events = review_fixture.client.get(
        "/api/v1/review/webhook-events?trial_id="
        f"{trial_id}&resource_id=provider-transfer-1&subscription=transfer"
        "&log_type=canceled&outcome=transfer_updated"
        "&from_at=2026-08-01T11:00:00%2B00:00&to_at=2026-08-01T13:00:00%2B00:00",
        headers=headers,
    )
    event_detail = review_fixture.client.get(
        f"/api/v1/review/webhook-events/{event_id}",
        headers=headers,
    )
    invoice_detail = review_fixture.client.get(
        f"/api/v1/review/invoices/{draft_id}", headers=headers
    )
    schedule = review_fixture.client.get(
        f"/api/v1/review/schedule?trial_id={trial_id}", headers=headers
    )

    # Then
    assert invoices.status_code == 200
    assert transfers.get_json()["data"][0]["provider_status"] == "canceled"
    assert detail.get_json()["data"]["net_amount_cents"] == 9950
    assert invoice_detail.get_json()["data"]["credit_status"] == "credited"
    assert trials.get_json()["data"][0]["id"] == trial_id
    assert trial_detail.get_json()["data"]["id"] == trial_id
    assert batches.get_json()["data"][0]["id"] == batch_id
    assert batch_detail.get_json()["data"]["id"] == batch_id
    assert events.get_json()["data"][0]["id"] == event_id
    assert event_detail.get_json()["data"]["id"] == event_id
    assert schedule.get_json()["data"]["trial"]["id"] == trial_id
    body = json.dumps(invoices.get_json()) + json.dumps(transfers.get_json())
    assert "Payer Must Not Leak" not in body
    assert "12345678901" not in body
    assert "hash-not-exposed" not in body


def test_review_api_exposes_rate_limit_after_burst(review_fixture: ReviewFixture) -> None:
    headers = {"Authorization": "Bearer review-token-that-is-long-enough-for-tests"}

    responses = [
        review_fixture.client.get("/api/v1/review/configuration", headers=headers)
        for _ in range(21)
    ]

    assert responses[-1].status_code == 429
    assert responses[-1].get_json() == {"error": "review_rate_limit_exceeded"}
    assert responses[-1].headers.get("Retry-After") is not None


def test_review_api_configuration_schedule_and_provider_read(review_fixture: ReviewFixture) -> None:
    headers = {"Authorization": "Bearer review-token-that-is-long-enough-for-tests"}

    configuration = review_fixture.client.get("/api/v1/review/configuration", headers=headers)
    schedule = review_fixture.client.get("/api/v1/review/schedule", headers=headers)
    provider = review_fixture.client.get("/api/v1/review/provider/webhooks", headers=headers)
    missing = review_fixture.client.get("/api/v1/review/trials/missing-trial", headers=headers)

    assert configuration.status_code == 200
    assert configuration.get_json()["data"]["webhook_subscriptions"] == ["invoice", "transfer"]
    assert schedule.status_code == 200
    assert schedule.get_json()["data"]["trial"] is None
    assert provider.status_code == 200
    assert provider.get_json() == {"data": {"webhooks": []}}
    assert missing.status_code == 404


def test_review_store_handles_cursor_and_empty_filtered_results(
    review_fixture: ReviewFixture,
) -> None:
    store = ReviewStore(review_fixture.engine)
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    with review_fixture.engine.begin() as connection:
        connection.execute(
            insert(trial_runs),
            [
                {
                    "id": "trial-cursor-1",
                    "status": "completed",
                    "started_at": now,
                    "ends_at": now,
                    "created_at": now,
                },
                {
                    "id": "trial-cursor-2",
                    "status": "completed",
                    "started_at": now,
                    "ends_at": now,
                    "created_at": now,
                },
            ],
        )

    page = store.trials(ReviewQuery(limit=1))
    next_page = store.trials(ReviewQuery(limit=1, cursor=page.next_cursor))
    naive_cursor = base64.urlsafe_b64encode(b"2026-08-01T12:00:00|trial-cursor-2").decode()
    naive_page = store.trials(ReviewQuery(limit=1, cursor=naive_cursor))
    not_observed = store.invoices(ReviewQuery(credit_status="not_observed"))
    transfers = store.transfers(ReviewQuery())

    assert len(page.items) == 1
    assert page.next_cursor is not None
    assert len(next_page.items) == 1
    assert len(naive_page.items) == 1
    aware = datetime(2026, 8, 1, 12, tzinfo=UTC)
    assert normalize_row_time(cast("RowMapping", {"at": aware}), "at") == aware
    assert not_observed.items == ()
    assert transfers.items == ()
    with pytest.raises(ValueError, match="invalid cursor"):
        store.trials(ReviewQuery(cursor="invalid"))


def test_review_api_reports_missing_provider(review_fixture: ReviewFixture) -> None:
    services = replace(review_fixture.services, provider=None)
    app = create_app(review_fixture.settings, services)
    response = app.test_client().get(
        "/api/v1/review/provider/webhooks",
        headers={"Authorization": "Bearer review-token-that-is-long-enough-for-tests"},
    )

    assert response.status_code == 503
    assert response.get_json() == {"error": "provider_unavailable"}


def test_review_api_sanitizes_provider_failures(review_fixture: ReviewFixture) -> None:
    headers = {"Authorization": "Bearer review-token-that-is-long-enough-for-tests"}

    review_fixture.gateway.webhook_error = ProviderTransientError(operation="list_webhooks")
    transient = review_fixture.client.get("/api/v1/review/provider/webhooks", headers=headers)
    review_fixture.gateway.webhook_error = ProviderError(operation="list_webhooks")
    permanent = review_fixture.client.get("/api/v1/review/provider/webhooks", headers=headers)

    assert transient.status_code == 503
    assert transient.get_json() == {"error": "provider_unavailable"}
    assert permanent.status_code == 502
    assert permanent.get_json() == {"error": "provider_query_failed"}
