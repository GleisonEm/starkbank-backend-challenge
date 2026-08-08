from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, insert, select

from starkbank_trial.application.payers import build_smoke_invoice
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
from starkbank_trial.persistence.schema import (
    metadata,
    owned_invoices,
    transfer_jobs,
    webhook_events,
)
from starkbank_trial.persistence.stores import Stores, build_stores


@pytest.fixture
def stores(tmp_path: Path) -> Iterator[Stores]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'trial.db'}")
    metadata.create_all(engine)
    yield build_stores(engine)
    engine.dispose()


def register_owned_invoice(stores: Stores, invoice_id: str = "invoice-1") -> None:
    draft = build_smoke_invoice("owned-1", 10_000, datetime(2026, 8, 1, 12, tzinfo=UTC))
    stores.invoices.record_smoke(draft, invoice_id, datetime(2026, 8, 1, 12, tzinfo=UTC))


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
    register_owned_invoice(stores)
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
    register_owned_invoice(stores)
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


def test_event_store_ignores_credit_for_invoice_created_by_other_environment(
    stores: Stores,
) -> None:
    # Given: the invoice was NOT registered in this environment (another
    # environment created it in the shared Sandbox workspace).
    received_at = datetime(2026, 8, 1, 12, tzinfo=UTC)
    foreign = CreditedInvoiceEvent(
        event_id=EventId("event-foreign-invoice"),
        invoice_id=InvoiceId("invoice-foreign"),
        amount=Cents(10_000),
        fee=Cents(50),
        workspace_id="workspace-1",
    )

    # When
    outcome = stores.events.record(EventRecord(foreign, "f" * 64, received_at, Cents(9_950)))

    # Then: audited as invoice_unknown, no transfer job is created.
    assert outcome is EventWriteResult.INVOICE_UNKNOWN
    with stores.transfers.engine.connect() as connection:
        job = connection.execute(select(func.count()).select_from(transfer_jobs)).scalar_one()
    assert job == 0
    with stores.transfers.engine.connect() as connection:
        stored = connection.execute(
            select(webhook_events.c.outcome).where(webhook_events.c.id == "event-foreign-invoice")
        ).scalar_one()
    assert stored == EventWriteResult.INVOICE_UNKNOWN


def test_event_store_queues_credit_for_invoice_created_by_this_environment(
    stores: Stores,
) -> None:
    # Given
    register_owned_invoice(stores, invoice_id="invoice-owned")
    received_at = datetime(2026, 8, 1, 12, tzinfo=UTC)
    owned = CreditedInvoiceEvent(
        event_id=EventId("event-owned"),
        invoice_id=InvoiceId("invoice-owned"),
        amount=Cents(10_000),
        fee=Cents(50),
        workspace_id="workspace-1",
    )

    # When
    outcome = stores.events.record(EventRecord(owned, "o" * 64, received_at, Cents(9_950)))

    # Then
    assert outcome == EventWriteResult.QUEUED
    with stores.transfers.engine.connect() as connection:
        job = connection.execute(select(func.count()).select_from(transfer_jobs)).scalar_one()
    assert job == 1


def test_event_store_uses_owned_tag_before_provider_id_is_persisted(stores: Stores) -> None:
    received_at = datetime(2026, 8, 1, 12, tzinfo=UTC)
    draft = build_smoke_invoice("race", 10_000, received_at, namespace="local")
    stores.invoices.register_smoke(draft, received_at)
    event = CreditedInvoiceEvent(
        event_id=EventId("event-race"),
        invoice_id=InvoiceId("invoice-race"),
        amount=Cents(10_000),
        fee=Cents(50),
        workspace_id="workspace-1",
        tags=(draft.tag,),
    )

    # When
    outcome = stores.events.record(EventRecord(event, "r" * 64, received_at, Cents(9_950)))

    # Then
    assert outcome is EventWriteResult.QUEUED
    with stores.invoices.engine.connect() as connection:
        owner = connection.execute(
            select(owned_invoices.c.provider_invoice_id).where(owned_invoices.c.tag == draft.tag)
        ).scalar_one()
        jobs = connection.execute(select(func.count()).select_from(transfer_jobs)).scalar_one()
    assert owner == "invoice-race"
    assert jobs == 1


def test_event_store_promotes_unknown_event_after_intent_is_registered(stores: Stores) -> None:
    # Given
    received_at = datetime(2026, 8, 1, 12, tzinfo=UTC)
    draft = build_smoke_invoice("late-registration", 10_000, received_at, namespace="local")
    event = CreditedInvoiceEvent(
        event_id=EventId("event-late-registration"),
        invoice_id=InvoiceId("invoice-late-registration"),
        amount=Cents(10_000),
        fee=Cents(50),
        workspace_id="workspace-1",
        tags=(draft.tag,),
    )
    record = EventRecord(event, "l" * 64, received_at, Cents(9_950))

    # When
    first = stores.events.record(record)
    stores.invoices.register_smoke(draft, received_at)
    replay = stores.events.record(record)
    second_replay = stores.events.record(record)

    # Then
    assert first is EventWriteResult.INVOICE_UNKNOWN
    assert replay is EventWriteResult.QUEUED
    assert second_replay is EventWriteResult.DUPLICATE_EVENT
    with stores.invoices.engine.connect() as connection:
        jobs = connection.execute(select(func.count()).select_from(transfer_jobs)).scalar_one()
        outcome = connection.execute(
            select(webhook_events.c.outcome).where(webhook_events.c.id == record.event.event_id)
        ).scalar_one()
    assert jobs == 1
    assert outcome == EventWriteResult.QUEUED


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
