from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, select

from starkbank_trial.application.schedule import build_schedule
from starkbank_trial.domain.events import (
    CreditedInvoiceEvent,
    EventRecord,
    EventWriteResult,
    TransferLifecycleEvent,
)
from starkbank_trial.domain.jobs import JobClaim
from starkbank_trial.domain.trials import BatchClaim, NewTrial
from starkbank_trial.domain.types import Cents, EventId, ExternalId, InvoiceId, TransferId
from starkbank_trial.persistence.schema import metadata, transfer_jobs, webhook_events
from starkbank_trial.persistence.stores import Stores, build_stores


@pytest.fixture
def stores(tmp_path: Path) -> Iterator[Stores]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'trial.db'}")
    metadata.create_all(engine)
    yield build_stores(engine)
    engine.dispose()


def test_trial_store_creates_and_claims_the_first_due_batch(stores: Stores) -> None:
    # Given
    start = datetime(2026, 8, 1, 12, tzinfo=UTC)
    schedule = build_schedule(start, (8, 9, 10, 11, 12, 8, 9, 10))
    trial = stores.trials.create(NewTrial(start_at=start, schedule=schedule))

    # When
    claimed = stores.trials.claim_due_batch(
        BatchClaim(now=start, lease_until=start + timedelta(minutes=5))
    )

    # Then
    assert trial.started_at == start
    assert claimed is not None
    assert claimed.slot_index == 0
    assert claimed.target_count == 8
    assert claimed.lease_until == start + timedelta(minutes=5)


def test_event_store_persists_one_job_for_duplicate_event(stores: Stores) -> None:
    # Given
    received_at = datetime(2026, 8, 1, 12, tzinfo=UTC)
    event = CreditedInvoiceEvent(
        event_id=EventId("event-1"),
        invoice_id=InvoiceId("invoice-1"),
        amount=Cents(10_000),
        fee=Cents(50),
        workspace_id="workspace-1",
    )
    record = EventRecord(
        event=event,
        payload_hash="a" * 64,
        received_at=received_at,
        net_amount=Cents(9_950),
    )

    # When
    first = stores.events.record(record)
    duplicate = stores.events.record(record)
    job = stores.transfers.claim(
        JobClaim(now=received_at, lease_until=received_at + timedelta(minutes=2))
    )
    second_job = stores.transfers.claim(
        JobClaim(now=received_at, lease_until=received_at + timedelta(minutes=2))
    )

    # Then
    assert first is EventWriteResult.QUEUED
    assert duplicate is EventWriteResult.DUPLICATE_EVENT
    assert job is not None
    assert job.invoice_id == InvoiceId("invoice-1")
    assert job.net_amount == Cents(9_950)
    assert second_job is None


def test_event_store_deduplicates_new_event_for_same_invoice(stores: Stores) -> None:
    # Given
    received_at = datetime(2026, 8, 1, 12, tzinfo=UTC)
    first_event = CreditedInvoiceEvent(
        event_id=EventId("event-1"),
        invoice_id=InvoiceId("invoice-1"),
        amount=Cents(10_000),
        fee=Cents(50),
        workspace_id="workspace-1",
    )
    second_event = CreditedInvoiceEvent(
        event_id=EventId("event-2"),
        invoice_id=InvoiceId("invoice-1"),
        amount=Cents(10_000),
        fee=Cents(50),
        workspace_id="workspace-1",
    )

    # When
    stores.events.record(EventRecord(first_event, "a" * 64, received_at, Cents(9_950)))
    result = stores.events.record(EventRecord(second_event, "b" * 64, received_at, Cents(9_950)))

    # Then
    assert result is EventWriteResult.DUPLICATE_INVOICE


def test_event_store_applies_new_transfer_status_and_ignores_stale_event(stores: Stores) -> None:
    received_at = datetime(2026, 8, 1, 12, tzinfo=UTC)
    with stores.transfers.engine.begin() as connection:
        connection.execute(
            insert(webhook_events).values(
                id="seed-event",
                subscription="invoice",
                log_type="credited",
                invoice_id="invoice-1",
                workspace_id="workspace-1",
                payload_hash="a" * 64,
                outcome="queued",
                received_at=received_at,
            )
        )
        connection.execute(
            insert(transfer_jobs).values(
                id="00000000-0000-0000-0000-000000000001",
                event_id="seed-event",
                invoice_id="invoice-1",
                amount=10_000,
                fee=50,
                net_amount=9_950,
                external_id="trial-transfer-invoice-1",
                tag="trial-transfer:invoice-1",
                status="succeeded",
                attempts=1,
                next_attempt_at=received_at,
                provider_status=None,
                created_at=received_at,
                updated_at=received_at,
            )
        )

    current = TransferLifecycleEvent(
        event_id=EventId("transfer-event-current"),
        transfer_id=TransferId("transfer-1"),
        external_id=ExternalId("trial-transfer-invoice-1"),
        status="success",
        log_type="success",
        updated_at=received_at,
        workspace_id="workspace-1",
    )
    stale = TransferLifecycleEvent(
        event_id=EventId("transfer-event-stale"),
        transfer_id=TransferId("transfer-1"),
        external_id=ExternalId("trial-transfer-invoice-1"),
        status="processing",
        log_type="processing",
        updated_at=received_at.replace(hour=11, minute=59),
        workspace_id="workspace-1",
    )

    updated = stores.events.record(EventRecord(current, "b" * 64, received_at, None))
    stale_result = stores.events.record(
        EventRecord(stale, "c" * 64, received_at.replace(minute=1), None)
    )

    with stores.transfers.engine.connect() as connection:
        status = connection.execute(
            select(transfer_jobs.c.provider_status, transfer_jobs.c.provider_log_type)
        ).one()

    assert updated is EventWriteResult.TRANSFER_UPDATED
    assert stale_result is EventWriteResult.TRANSFER_STALE
    assert status == ("success", "success")
