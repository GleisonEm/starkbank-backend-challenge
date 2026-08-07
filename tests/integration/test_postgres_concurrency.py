import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from starkbank_trial.application.payers import build_smoke_invoice
from starkbank_trial.domain.events import CreditedInvoiceEvent, EventRecord, EventWriteResult
from starkbank_trial.domain.jobs import JobClaim
from starkbank_trial.domain.types import Cents, EventId, InvoiceId
from starkbank_trial.persistence.engine import build_engine
from starkbank_trial.persistence.schema import metadata, transfer_jobs
from starkbank_trial.persistence.stores import Stores, build_stores


@pytest.fixture
def postgres_stores() -> Iterator[Stores]:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    engine = build_engine(database_url)
    metadata.drop_all(engine)
    metadata.create_all(engine)
    stores = build_stores(engine)
    stores.invoices.record_smoke(
        build_smoke_invoice("owned-1", 10_000, datetime(2026, 8, 1, 12, tzinfo=UTC)),
        "invoice-1",
        datetime(2026, 8, 1, 12, tzinfo=UTC),
    )
    yield stores
    metadata.drop_all(engine)
    engine.dispose()


def record(event_id: str, received_at: datetime) -> EventRecord:
    event = CreditedInvoiceEvent(
        event_id=EventId(event_id),
        invoice_id=InvoiceId("invoice-1"),
        amount=Cents(10_000),
        fee=Cents(50),
        workspace_id="workspace-1",
    )
    return EventRecord(event, event_id.zfill(64), received_at, Cents(9_950))


@pytest.mark.integration
def test_concurrent_deliveries_create_one_transfer_job(postgres_stores: Stores) -> None:
    # Given
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    records = (record("event-1", now), record("event-2", now))

    # When
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(postgres_stores.events.record, records))

    # Then
    assert set(outcomes) == {
        EventWriteResult.QUEUED,
        EventWriteResult.DUPLICATE_INVOICE,
    }
    with postgres_stores.transfers.engine.connect() as connection:
        job_count = connection.execute(select(func.count()).select_from(transfer_jobs)).scalar_one()
    assert job_count == 1


@pytest.mark.integration
def test_concurrent_workers_claim_a_job_once(postgres_stores: Stores) -> None:
    # Given
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    postgres_stores.events.record(record("event-1", now))
    claim = JobClaim(now=now, lease_until=now + timedelta(minutes=2))

    # When
    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = tuple(executor.map(postgres_stores.transfers.claim, (claim, claim)))

    # Then
    assert sum(job is not None for job in jobs) == 1
