from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select

from starkbank_trial.application.payers import build_invoice_drafts
from starkbank_trial.application.trials import TrialService, TrialTickResult
from starkbank_trial.domain.invoices import InvoiceDraft, ProviderInvoice
from starkbank_trial.domain.provider import (
    ProviderTimeoutError,
    ProviderTransientError,
    ProviderUnknownOutcomeError,
)
from starkbank_trial.domain.status import BatchStatus, DraftStatus, TrialStatus
from starkbank_trial.domain.trials import BatchClaim, InvoiceBatch
from starkbank_trial.domain.types import BatchId, InvoiceId, TrialRunId
from starkbank_trial.persistence.schema import invoice_batches, invoice_drafts, metadata, trial_runs
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


def test_invoice_retries_are_bounded_and_end_in_failed(stores: Stores) -> None:
    class AlwaysTransientProvider(RecordingInvoiceProvider):
        def create_invoice(self, draft: InvoiceDraft) -> ProviderInvoice:
            self.created.append(draft)
            raise ProviderTransientError(operation="invoice.create")

    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    clock = FixedClock(now)
    provider = AlwaysTransientProvider()
    service = TrialService(
        stores,
        provider,
        clock,
        FixedBatchCounts((8,) * 8),
        retry_base_seconds=5,
        invoice_max_attempts=5,
    )
    service.start(now)

    for delay in (0, 5, 15, 35, 75):
        clock.value = now + timedelta(seconds=delay)
        service.tick()

    with stores.invoices.engine.connect() as connection:
        rows = connection.execute(
            select(
                invoice_drafts.c.status,
                invoice_drafts.c.attempts,
                invoice_drafts.c.last_error_code,
            )
        ).all()
    assert len(rows) == 8
    assert all(row.status == DraftStatus.FAILED for row in rows)
    assert all(row.attempts == 5 for row in rows)
    assert all(row.last_error_code == "retry_exhausted" for row in rows)


class _LateTagProvider(RecordingInvoiceProvider):
    def __init__(self, *, tag_visible: bool) -> None:
        super().__init__()
        self.tag_visible = tag_visible

    def create_invoice(self, draft: InvoiceDraft) -> ProviderInvoice:
        self.created.append(draft)
        self.known_by_tag[draft.tag] = ProviderInvoice(
            id=InvoiceId(f"provider-{draft.ordinal}"), tag=draft.tag
        )
        raise ProviderUnknownOutcomeError(operation="invoice.create")

    def find_invoice(self, tag: str) -> ProviderInvoice | None:
        if self.tag_visible:
            return self.known_by_tag.get(tag)
        return None


def _exhaust_reconciliation(
    service: TrialService,
    clock: FixedClock,
) -> None:
    for _ in range(5):
        service.reconcile_invoices()
        clock.value += timedelta(seconds=11)


def test_retried_draft_is_found_by_tag_before_recreate(stores: Stores) -> None:
    # Given
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    clock = FixedClock(now)
    provider = _LateTagProvider(tag_visible=False)
    service = TrialService(
        stores,
        provider,
        clock,
        FixedBatchCounts((8,) * 8),
        retry_base_seconds=5,
        invoice_max_attempts=5,
        invoice_reconciliation_max_attempts=5,
    )
    service.start(now)
    assert service.tick() is TrialTickResult.RECONCILING
    _exhaust_reconciliation(service, clock)
    provider.tag_visible = True

    # When
    clock.value += timedelta(hours=1)
    result = service.tick()

    # Then
    assert result is TrialTickResult.COMPLETED
    assert len(provider.created) == 8
    with stores.invoices.engine.connect() as connection:
        rows = connection.execute(
            select(
                invoice_drafts.c.status,
                invoice_drafts.c.attempts,
                invoice_drafts.c.provider_invoice_id,
            )
        ).all()
    assert all(row.status == DraftStatus.CREATED for row in rows)
    assert all(row.attempts == 2 for row in rows)
    assert all(row.provider_invoice_id is not None for row in rows)


def test_draft_with_missing_tag_is_created_again(stores: Stores) -> None:
    # Given
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    clock = FixedClock(now)
    provider = _LateTagProvider(tag_visible=False)
    service = TrialService(
        stores,
        provider,
        clock,
        FixedBatchCounts((8,) * 8),
        retry_base_seconds=5,
        invoice_max_attempts=5,
        invoice_reconciliation_max_attempts=5,
    )
    service.start(now)
    assert service.tick() is TrialTickResult.RECONCILING
    _exhaust_reconciliation(service, clock)

    # When
    clock.value += timedelta(hours=1)
    result = service.tick()

    # Then
    assert result is TrialTickResult.RECONCILING
    assert len(provider.created) == 16
    with stores.invoices.engine.connect() as connection:
        attempts = (
            connection.execute(select(invoice_drafts.c.attempts).order_by(invoice_drafts.c.ordinal))
            .scalars()
            .all()
        )
    assert attempts == [2] * 8


def test_batch_claims_are_bounded_and_exhausted_batch_is_degraded(stores: Stores) -> None:
    # Given
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    service = TrialService(
        stores,
        RecordingInvoiceProvider(),
        FixedClock(now),
        FixedBatchCounts((8,) * 8),
    )
    service.start(now)

    # When
    claimed: list[bool] = []
    for offset in (0, 301, 602):
        claim_time = now + timedelta(seconds=offset)
        batch = stores.trials.claim_due_batch(
            BatchClaim(
                now=claim_time,
                lease_until=claim_time + timedelta(seconds=300),
                max_attempts=2,
            )
        )
        claimed.append(batch is not None)

    # Then
    assert claimed == [True, True, False]
    with stores.trials.engine.connect() as connection:
        status = connection.execute(
            select(invoice_batches.c.status).order_by(invoice_batches.c.scheduled_at).limit(1)
        ).scalar_one()
        trial_status = connection.execute(select(trial_runs.c.status)).scalar_one()
    assert status == BatchStatus.DEGRADED
    assert trial_status == TrialStatus.DEGRADED


def test_report_skips_degraded_batches_for_next_batch(stores: Stores) -> None:
    # Given
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    service = TrialService(
        stores,
        RecordingInvoiceProvider(),
        FixedClock(now),
        FixedBatchCounts((8,) * 8),
    )
    service.start(now)
    for offset in (0, 301, 602):
        claim_time = now + timedelta(seconds=offset)
        stores.trials.claim_due_batch(
            BatchClaim(
                now=claim_time,
                lease_until=claim_time + timedelta(seconds=300),
                max_attempts=2,
            )
        )

    # When
    report = stores.trials.report()

    # Then
    assert report.trial is not None
    assert report.trial.next_batch_at == now + timedelta(hours=3)
