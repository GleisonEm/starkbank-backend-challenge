from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select

from starkbank_trial.application.smoke import SmokeBatchService
from starkbank_trial.application.transfers import TransferWorker, WorkerResult
from starkbank_trial.application.trials import TrialService, TrialTickResult
from starkbank_trial.domain.errors import LiveOperationsDisabledError
from starkbank_trial.domain.invoices import InvoiceDraft, ProviderInvoice
from starkbank_trial.domain.models import TransferCommand
from starkbank_trial.domain.provider import ProviderTransfer, ProviderUnknownOutcomeError
from starkbank_trial.domain.types import InvoiceId, TransferId
from starkbank_trial.persistence.schema import metadata, transfer_jobs
from starkbank_trial.persistence.stores import Stores, build_stores


@dataclass(frozen=True, slots=True)
class FixedClock:
    current: datetime

    def now(self) -> datetime:
        return self.current


@dataclass(frozen=True, slots=True)
class FixedBatchCounts:
    def next(self) -> tuple[int, ...]:
        return (8,) * 8


@dataclass(slots=True)
class InvoiceProvider:
    created: list[InvoiceDraft] = field(default_factory=list)
    known: dict[str, ProviderInvoice] = field(default_factory=dict)
    unknown_once: bool = False

    def create_invoice(self, draft: InvoiceDraft) -> ProviderInvoice:
        self.created.append(draft)
        invoice = ProviderInvoice(InvoiceId(f"invoice-{len(self.created)}"), draft.tag)
        self.known[draft.tag] = invoice
        if self.unknown_once:
            self.unknown_once = False
            raise ProviderUnknownOutcomeError(operation="invoice.create")
        return invoice

    def find_invoice(self, tag: str) -> ProviderInvoice | None:
        return self.known.get(tag)


@dataclass(slots=True)
class TransferProvider:
    commands: list[TransferCommand] = field(default_factory=list)

    def ensure_transfer(self, command: TransferCommand) -> ProviderTransfer:
        self.commands.append(command)
        return ProviderTransfer(TransferId("transfer-1"), command.external_id, "created")

    def find_transfer(self, command: TransferCommand) -> ProviderTransfer | None:
        return None


@pytest.fixture
def stores(tmp_path: Path) -> Iterator[Stores]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'operations.db'}")
    metadata.create_all(engine)
    yield build_stores(engine)
    engine.dispose()


def test_disabled_trial_cannot_start_or_claim_a_batch(stores: Stores) -> None:
    provider = InvoiceProvider()
    service = TrialService(
        stores,
        provider,
        FixedClock(datetime(2026, 8, 1, 12, tzinfo=UTC)),
        FixedBatchCounts(),
        live_operations_enabled=False,
    )

    with pytest.raises(LiveOperationsDisabledError):
        service.start()

    assert service.tick() is TrialTickResult.LIVE_DISABLED
    assert provider.created == []


def test_disabled_worker_does_not_claim_a_transfer(stores: Stores) -> None:
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    with stores.transfers.engine.begin() as connection:
        connection.execute(
            transfer_jobs.insert().values(
                id="job-1",
                event_id="event-1",
                invoice_id="invoice-1",
                amount=10_000,
                fee=50,
                net_amount=9_950,
                external_id="external-1",
                tag="tag-1",
                status="pending",
                attempts=0,
                next_attempt_at=now,
                lease_until=None,
                provider_transfer_id=None,
                last_error_code=None,
                metadata=None,
                created_at=now,
                updated_at=now,
            )
        )

    provider = TransferProvider()
    worker = TransferWorker(
        stores.transfers,
        provider,
        FixedClock(now),
        live_operations_enabled=False,
    )

    assert worker.process_one() is WorkerResult.LIVE_DISABLED
    assert provider.commands == []
    with stores.transfers.engine.connect() as connection:
        assert connection.execute(select(transfer_jobs.c.status)).scalar_one() == "pending"


def test_smoke_batch_is_idempotent_by_reference() -> None:
    provider = InvoiceProvider()
    clock = FixedClock(datetime(2026, 8, 1, 12, tzinfo=UTC))
    service = SmokeBatchService(provider, clock)

    first = service.run("submission-check", 8, 10_000)
    second = service.run("submission-check", 8, 10_000)

    assert len(first.invoices) == 8
    assert first.reused == 0
    assert len(second.invoices) == 8
    assert second.reused == 8
    assert len(provider.created) == 8


def test_smoke_batch_reconciles_an_ambiguous_create() -> None:
    provider = InvoiceProvider(unknown_once=True)
    service = SmokeBatchService(provider, FixedClock(datetime(2026, 8, 1, 12, tzinfo=UTC)))

    result = service.run("ambiguous-create", 1, 10_000)

    assert len(result.invoices) == 1
    assert result.reused == 0
    assert len(provider.created) == 1
