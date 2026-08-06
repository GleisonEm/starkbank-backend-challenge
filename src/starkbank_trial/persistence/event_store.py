from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Engine, func, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from starkbank_trial.domain.events import (
    CreditedInvoiceEvent,
    EventRecord,
    EventWriteResult,
    IgnoredEvent,
)
from starkbank_trial.domain.status import TransferStatus
from starkbank_trial.domain.transfer import build_transfer_command
from starkbank_trial.domain.types import Cents, EventId
from starkbank_trial.persistence.schema import transfer_jobs, webhook_events


@dataclass(frozen=True, slots=True)
class EventStore:
    engine: Engine

    def record(self, record: EventRecord) -> EventWriteResult:
        outcome = self._initial_outcome(record)
        with self.engine.begin() as connection:
            if not self._insert_event(connection, record, outcome):
                return EventWriteResult.DUPLICATE_EVENT
            if isinstance(record.event, IgnoredEvent) or record.net_amount is None:
                return outcome
            return self._queue_transfer(
                connection,
                record.event,
                record.net_amount,
                record.received_at,
            )

    @staticmethod
    def _initial_outcome(record: EventRecord) -> EventWriteResult:
        if isinstance(record.event, IgnoredEvent):
            return EventWriteResult.IGNORED
        if record.net_amount is None:
            return EventWriteResult.REJECTED
        return EventWriteResult.QUEUED

    @staticmethod
    def _insert_event(
        connection: Connection,
        record: EventRecord,
        outcome: EventWriteResult,
    ) -> bool:
        event = record.event
        invoice_id = event.invoice_id if isinstance(event, CreditedInvoiceEvent) else None
        try:
            with connection.begin_nested():
                connection.execute(
                    insert(webhook_events).values(
                        id=event.event_id,
                        subscription=event.subscription,
                        log_type=event.log_type,
                        invoice_id=invoice_id,
                        workspace_id=event.workspace_id,
                        payload_hash=record.payload_hash,
                        outcome=outcome,
                        received_at=record.received_at,
                    )
                )
        except IntegrityError:
            return False
        return True

    def _queue_transfer(
        self,
        connection: Connection,
        event: CreditedInvoiceEvent,
        net_amount: Cents,
        received_at: datetime,
    ) -> EventWriteResult:
        if (
            connection.execute(
                select(transfer_jobs.c.id).where(transfer_jobs.c.invoice_id == event.invoice_id)
            ).first()
            is not None
        ):
            return self._mark_duplicate(connection, event.event_id)
        command = build_transfer_command(event.invoice_id, net_amount)
        try:
            with connection.begin_nested():
                connection.execute(
                    insert(transfer_jobs).values(
                        id=str(uuid4()),
                        event_id=event.event_id,
                        invoice_id=event.invoice_id,
                        amount=event.amount,
                        fee=event.fee,
                        net_amount=net_amount,
                        external_id=command.external_id,
                        tag=command.tag,
                        status=TransferStatus.PENDING,
                        attempts=0,
                        next_attempt_at=received_at,
                        created_at=received_at,
                        updated_at=received_at,
                    )
                )
        except IntegrityError:
            return self._mark_duplicate(connection, event.event_id)
        if self.engine.dialect.name == "postgresql":
            connection.execute(select(func.pg_notify("transfer_jobs", str(event.invoice_id))))
        return EventWriteResult.QUEUED

    @staticmethod
    def _mark_duplicate(connection: Connection, event_id: EventId) -> EventWriteResult:
        connection.execute(
            update(webhook_events)
            .where(webhook_events.c.id == event_id)
            .values(outcome=EventWriteResult.DUPLICATE_INVOICE)
        )
        return EventWriteResult.DUPLICATE_INVOICE
