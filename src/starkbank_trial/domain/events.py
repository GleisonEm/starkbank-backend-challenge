from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique

from starkbank_trial.domain.types import Cents, EventId, InvoiceId


@dataclass(frozen=True, slots=True)
class CreditedInvoiceEvent:
    event_id: EventId
    invoice_id: InvoiceId
    amount: Cents
    fee: Cents
    workspace_id: str
    subscription: str = "invoice"
    log_type: str = "credited"


@dataclass(frozen=True, slots=True)
class IgnoredEvent:
    event_id: EventId
    subscription: str
    log_type: str
    workspace_id: str


type VerifiedEvent = CreditedInvoiceEvent | IgnoredEvent


@dataclass(frozen=True, slots=True)
class EventRecord:
    event: VerifiedEvent
    payload_hash: str
    received_at: datetime
    net_amount: Cents | None


@unique
class EventWriteResult(StrEnum):
    QUEUED = "queued"
    IGNORED = "ignored"
    REJECTED = "rejected"
    DUPLICATE_EVENT = "duplicate_event"
    DUPLICATE_INVOICE = "duplicate_invoice"
