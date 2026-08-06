from dataclasses import dataclass
from datetime import datetime

from starkbank_trial.domain.status import TransferStatus
from starkbank_trial.domain.types import Cents, EventId, ExternalId, InvoiceId, TransferJobId


@dataclass(frozen=True, slots=True)
class JobClaim:
    now: datetime
    lease_until: datetime


@dataclass(frozen=True, slots=True)
class TransferJob:
    id: TransferJobId
    event_id: EventId
    invoice_id: InvoiceId
    amount: Cents
    fee: Cents
    net_amount: Cents
    external_id: ExternalId
    tag: str
    status: TransferStatus
    attempts: int
    lease_until: datetime
