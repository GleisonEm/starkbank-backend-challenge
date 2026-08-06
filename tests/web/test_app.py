import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from flask.testing import FlaskClient
from sqlalchemy import Engine, create_engine

from starkbank_trial.application.transfers import TransferWorker
from starkbank_trial.application.trials import BatchCounts, TrialService
from starkbank_trial.application.webhooks import WebhookService
from starkbank_trial.bootstrap import Services
from starkbank_trial.config import Settings
from starkbank_trial.domain.events import CreditedInvoiceEvent, VerifiedEvent
from starkbank_trial.domain.invoices import InvoiceDraft, ProviderInvoice
from starkbank_trial.domain.models import TransferCommand
from starkbank_trial.domain.provider import InvalidWebhookError, ProviderTransfer
from starkbank_trial.domain.types import Cents, EventId, InvoiceId
from starkbank_trial.http import create_app
from starkbank_trial.persistence.schema import metadata
from starkbank_trial.persistence.stores import build_stores

if TYPE_CHECKING:
    from starkbank_trial.application.clock import Clock


@dataclass(frozen=True, slots=True)
class FixedClock:
    current: datetime

    def now(self) -> datetime:
        return self.current


@dataclass(slots=True)
class FakeGateway:
    event: VerifiedEvent
    invalid: bool = False
    transfers: list[TransferCommand] = field(default_factory=list)

    def create_invoice(self, draft: InvoiceDraft) -> ProviderInvoice:
        raise NotImplementedError

    def find_invoice(self, tag: str) -> ProviderInvoice | None:
        raise NotImplementedError

    def ensure_transfer(self, command: TransferCommand) -> ProviderTransfer:
        self.transfers.append(command)
        raise NotImplementedError

    def verify_event(self, content: bytes, signature: str) -> VerifiedEvent:
        if self.invalid:
            raise InvalidWebhookError(operation="verify_event")
        return self.event


@dataclass(frozen=True, slots=True)
class FixedCounts:
    def next(self) -> tuple[int, ...]:
        return (8, 8, 8, 8, 8, 8, 8, 8)


@dataclass(frozen=True, slots=True)
class AppFixture:
    client: FlaskClient
    engine: Engine
    gateway: FakeGateway
    log_file: Path


@pytest.fixture
def app_fixture(tmp_path: Path) -> Iterator[AppFixture]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'http.db'}")
    metadata.create_all(engine)
    stores = build_stores(engine)
    event = CreditedInvoiceEvent(
        event_id=EventId("event-1"),
        invoice_id=InvoiceId("invoice-1"),
        amount=Cents(10_000),
        fee=Cents(50),
        workspace_id="workspace-1",
    )
    gateway = FakeGateway(event)
    clock: Clock = FixedClock(datetime(2026, 8, 1, 12, tzinfo=UTC))
    counts: BatchCounts = FixedCounts()
    log_file = tmp_path / "logs" / "http.jsonl"
    services = Services(
        engine=engine,
        stores=stores,
        trial=TrialService(stores, gateway, clock, counts),
        webhook=WebhookService(gateway, stores.events, clock),
        worker=TransferWorker(stores.transfers, gateway, clock),
    )
    settings = Settings.model_validate(
        {
            "DATABASE_URL": str(engine.url),
            "MAX_CONTENT_LENGTH": 64,
            "LOG_LEVEL": "info",
            "LOG_FILE": str(log_file),
        }
    )
    app = create_app(settings, services)
    app.testing = True
    yield AppFixture(app.test_client(), engine, gateway, log_file)
    engine.dispose()


def test_health_endpoints_report_live_and_ready(app_fixture: AppFixture) -> None:
    # Given / When
    live = app_fixture.client.get("/health/live")
    ready = app_fixture.client.get("/health/ready")

    # Then
    assert live.status_code == 200
    assert live.get_json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.get_json() == {"status": "ready"}


def test_webhook_requires_signature_and_queues_without_transfer_call(
    app_fixture: AppFixture,
) -> None:
    # Given / When
    missing = app_fixture.client.post("/webhooks/starkbank", data=b"payload")
    accepted = app_fixture.client.post(
        "/webhooks/starkbank",
        data=b"payload",
        headers={"Digital-Signature": "valid"},
    )

    # Then
    assert missing.status_code == 400
    assert accepted.status_code == 200
    assert accepted.get_json() == {"status": "queued"}
    assert app_fixture.gateway.transfers == []


def test_webhook_rejects_invalid_signature(app_fixture: AppFixture) -> None:
    # Given
    app_fixture.gateway.invalid = True

    # When
    response = app_fixture.client.post(
        "/webhooks/starkbank",
        data=b"payload",
        headers={"Digital-Signature": "invalid"},
    )

    # Then
    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid webhook"}


def test_webhook_enforces_body_limit(app_fixture: AppFixture) -> None:
    # Given / When
    response = app_fixture.client.post(
        "/webhooks/starkbank",
        data=b"x" * 65,
        headers={"Digital-Signature": "valid"},
    )

    # Then
    assert response.status_code == 413


def test_webhook_persists_redacted_state_transition_logs(app_fixture: AppFixture) -> None:
    # Given / When
    response = app_fixture.client.post(
        "/webhooks/starkbank",
        data=b"payload",
        headers={"Digital-Signature": "valid"},
    )

    # Then
    assert response.status_code == 200
    content = app_fixture.log_file.read_text(encoding="utf-8")
    entries = [json.loads(line) for line in content.splitlines()]
    assert [(entry["event"], entry.get("outcome")) for entry in entries] == [
        ("webhook_received", None),
        ("webhook_recorded", "queued"),
    ]
    assert entries[0]["content_length"] == 7
    assert "payload" not in content
    assert "valid" not in content
