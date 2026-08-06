import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select

from starkbank_trial.application.transfers import TransferWorker, WorkerResult
from starkbank_trial.application.webhooks import WebhookService
from starkbank_trial.domain.constants import STARK_BANK_RECIPIENT
from starkbank_trial.domain.events import CreditedInvoiceEvent, EventWriteResult, VerifiedEvent
from starkbank_trial.domain.models import TransferCommand
from starkbank_trial.domain.provider import (
    ProviderTimeoutError,
    ProviderTransfer,
    ProviderTransientError,
)
from starkbank_trial.domain.status import TransferStatus
from starkbank_trial.domain.types import Cents, EventId, ExternalId, InvoiceId, TransferId
from starkbank_trial.logging import configure_logging
from starkbank_trial.persistence.schema import metadata, transfer_jobs
from starkbank_trial.persistence.stores import Stores, build_stores


@dataclass(slots=True)
class FixedClock:
    current: datetime

    def now(self) -> datetime:
        return self.current


@dataclass(frozen=True, slots=True)
class FixedVerifier:
    event: VerifiedEvent

    def verify_event(self, content: bytes, signature: str) -> VerifiedEvent:
        return self.event


@dataclass(slots=True)
class RecordingTransferProvider:
    timeout_once: bool = False
    commands: list[TransferCommand] = field(default_factory=list)
    remote: dict[ExternalId, ProviderTransfer] = field(default_factory=dict)

    def ensure_transfer(self, command: TransferCommand) -> ProviderTransfer:
        existing = self.remote.get(command.external_id)
        if existing is not None:
            return existing
        self.commands.append(command)
        created = ProviderTransfer(
            id=TransferId("transfer-1"),
            external_id=command.external_id,
            status="created",
        )
        self.remote[command.external_id] = created
        if self.timeout_once:
            self.timeout_once = False
            raise ProviderTimeoutError(operation="create_transfer")
        return created

    def find_transfer(self, command: TransferCommand) -> ProviderTransfer | None:
        return self.remote.get(command.external_id)


@pytest.fixture
def stores(tmp_path: Path) -> Iterator[Stores]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'trial.db'}")
    metadata.create_all(engine)
    yield build_stores(engine)
    engine.dispose()


def credited_event(*, amount: int = 10_000, fee: int = 50) -> CreditedInvoiceEvent:
    return CreditedInvoiceEvent(
        event_id=EventId("event-1"),
        invoice_id=InvoiceId("invoice-1"),
        amount=Cents(amount),
        fee=Cents(fee),
        workspace_id="workspace-1",
    )


def test_credited_webhook_queues_exactly_one_net_transfer(stores: Stores) -> None:
    # Given
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    clock = FixedClock(now)
    webhook = WebhookService(FixedVerifier(credited_event()), stores.events, clock)
    provider = RecordingTransferProvider()
    worker = TransferWorker(stores.transfers, provider, clock)

    # When
    first = webhook.receive(b'{"event": "signed-payload"}', "signature")
    duplicate = webhook.receive(b'{"event": "signed-payload"}', "signature")
    processed = worker.process_one()
    idle = worker.process_one()

    # Then
    assert first is EventWriteResult.QUEUED
    assert duplicate is EventWriteResult.DUPLICATE_EVENT
    assert processed is WorkerResult.SUCCEEDED
    assert idle is WorkerResult.IDLE
    assert len(provider.commands) == 1
    assert provider.commands[0].amount == Cents(9_950)
    assert provider.commands[0].recipient == STARK_BANK_RECIPIENT
    with stores.transfers.engine.connect() as connection:
        status = connection.execute(select(transfer_jobs.c.status)).scalar_one()
    assert status == TransferStatus.SUCCEEDED


def test_invalid_net_amount_is_audited_without_transfer(stores: Stores) -> None:
    # Given
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    clock = FixedClock(now)
    webhook = WebhookService(FixedVerifier(credited_event(amount=50, fee=50)), stores.events, clock)
    provider = RecordingTransferProvider()
    worker = TransferWorker(stores.transfers, provider, clock)

    # When
    outcome = webhook.receive(b"payload", "signature")

    # Then
    assert outcome is EventWriteResult.REJECTED
    assert worker.process_one() is WorkerResult.IDLE
    assert provider.commands == []


def test_timeout_reconciles_remote_transfer_without_duplicate(stores: Stores) -> None:
    # Given
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    clock = FixedClock(now)
    webhook = WebhookService(FixedVerifier(credited_event()), stores.events, clock)
    provider = RecordingTransferProvider(timeout_once=True)
    worker = TransferWorker(stores.transfers, provider, clock, retry_base_seconds=5)
    webhook.receive(b"payload", "signature")

    # When
    unknown = worker.process_one()
    clock.current += timedelta(seconds=5)
    reconciled = worker.process_one()

    # Then
    assert unknown is WorkerResult.RECONCILIATION_SCHEDULED
    assert reconciled is WorkerResult.SUCCEEDED
    assert len(provider.commands) == 1


def test_transfer_worker_persists_success_log(stores: Stores, tmp_path: Path) -> None:
    # Given
    log_file = tmp_path / "logs" / "worker.jsonl"
    configure_logging("info", log_file)
    clock = FixedClock(datetime(2026, 8, 1, 12, tzinfo=UTC))
    webhook = WebhookService(FixedVerifier(credited_event()), stores.events, clock)
    worker = TransferWorker(stores.transfers, RecordingTransferProvider(), clock)
    webhook.receive(b"payload", "signature")

    # When
    result = worker.process_one()

    # Then
    assert result is WorkerResult.SUCCEEDED
    entry = json.loads(log_file.read_text(encoding="utf-8"))
    assert entry["event"] == "transfer_succeeded"
    assert entry["invoice_id"] == "invoice-1"
    assert entry["provider_transfer_id"] == "transfer-1"


def test_transfer_retries_are_bounded_and_final_lookup_cannot_duplicate(
    stores: Stores,
) -> None:
    @dataclass(slots=True)
    class AlwaysTransientProvider:
        commands: list[TransferCommand] = field(default_factory=list)

        def ensure_transfer(self, command: TransferCommand) -> ProviderTransfer:
            self.commands.append(command)
            raise ProviderTransientError(operation="transfer.create")

        def find_transfer(self, command: TransferCommand) -> ProviderTransfer | None:
            return None

    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    clock = FixedClock(now)
    webhook = WebhookService(FixedVerifier(credited_event()), stores.events, clock)
    provider = AlwaysTransientProvider()
    worker = TransferWorker(
        stores.transfers,
        provider,
        clock,
        retry_base_seconds=5,
        transfer_max_attempts=3,
    )
    webhook.receive(b"payload", "signature")

    for delay in (0, 5, 15):
        clock.current = now + timedelta(seconds=delay)
        worker.process_one()

    with stores.transfers.engine.connect() as connection:
        row = connection.execute(
            select(
                transfer_jobs.c.status,
                transfer_jobs.c.attempts,
                transfer_jobs.c.last_error_code,
            )
        ).one()
    assert row.status == TransferStatus.PERMANENT_FAILURE
    assert row.attempts == 3
    assert row.last_error_code == "retry_exhausted"
    assert len(provider.commands) == 3
