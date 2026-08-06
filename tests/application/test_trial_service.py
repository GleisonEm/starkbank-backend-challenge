from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from starkbank_trial.application.payers import build_invoice_drafts
from starkbank_trial.application.trials import TrialService, TrialTickResult
from starkbank_trial.domain.invoices import InvoiceDraft, ProviderInvoice
from starkbank_trial.domain.provider import ProviderTimeoutError
from starkbank_trial.domain.status import BatchStatus
from starkbank_trial.domain.trials import InvoiceBatch
from starkbank_trial.domain.types import BatchId, InvoiceId, TrialRunId
from starkbank_trial.persistence.schema import metadata
from starkbank_trial.persistence.stores import Stores, build_stores


class RecordingInvoiceProvider:
    def __init__(self) -> None:
        self.created: list[InvoiceDraft] = []
        self.timeout_once = False
        self.known_by_tag: dict[str, ProviderInvoice] = {}

    def create_invoice(self, draft: InvoiceDraft) -> ProviderInvoice:
        self.created.append(draft)
        invoice = ProviderInvoice(id=InvoiceId(f"provider-{draft.ordinal}"), tag=draft.tag)
        self.known_by_tag[draft.tag] = invoice
        if self.timeout_once:
            self.timeout_once = False
            raise ProviderTimeoutError(operation="invoice.create")
        return invoice

    def find_invoice(self, tag: str) -> ProviderInvoice | None:
        return self.known_by_tag.get(tag)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.value = now

    def now(self) -> datetime:
        return self.value


class FixedBatchCounts:
    def __init__(self, counts: tuple[int, ...]) -> None:
        self.counts = counts

    def next(self) -> tuple[int, ...]:
        return self.counts


@pytest.fixture
def stores(tmp_path: Path) -> Iterator[Stores]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'trial-service.db'}")
    metadata.create_all(engine)
    yield build_stores(engine)
    engine.dispose()


def test_build_invoice_drafts_is_deterministic_for_a_batch() -> None:
    # Given
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    batch = InvoiceBatch(
        id=BatchId("batch-1"),
        run_id=TrialRunId("run-1"),
        slot_index=0,
        scheduled_at=now,
        target_count=8,
        status=BatchStatus.ISSUING,
        lease_until=now,
        attempts=1,
    )

    # When
    first = build_invoice_drafts(batch, now)
    second = build_invoice_drafts(batch, now)

    # Then
    assert first == second
    assert len(first) == 8
    assert all(1_000 <= draft.amount <= 10_000 for draft in first)


def test_trial_tick_issues_first_batch_once(stores: Stores) -> None:
    # Given
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    provider = RecordingInvoiceProvider()
    service = TrialService(stores, provider, FixedClock(now), FixedBatchCounts((8,) * 8))
    service.start(now)

    # When
    first_result = service.tick()
    second_result = service.tick()

    # Then
    assert first_result is TrialTickResult.COMPLETED
    assert second_result is TrialTickResult.NO_BATCH
    assert len(provider.created) == 8


def test_trial_reconciles_timeout_by_tag_without_recreating_invoice(stores: Stores) -> None:
    # Given
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    provider = RecordingInvoiceProvider()
    provider.timeout_once = True
    service = TrialService(stores, provider, FixedClock(now), FixedBatchCounts((8,) * 8))
    service.start(now)
    service.tick()

    # When
    reconciled = service.reconcile_invoices()

    # Then
    assert reconciled == 1
    assert len(provider.created) == 8
    assert service.tick() is TrialTickResult.NO_BATCH
