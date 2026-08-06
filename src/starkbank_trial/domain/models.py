from dataclasses import dataclass
from datetime import datetime

from starkbank_trial.domain.types import Cents, ExternalId, InvoiceId


@dataclass(frozen=True, slots=True)
class Recipient:
    bank_code: str
    branch_code: str
    account_number: str
    name: str
    tax_id: str
    account_type: str


@dataclass(frozen=True, slots=True)
class TransferCommand:
    invoice_id: InvoiceId
    amount: Cents
    external_id: ExternalId
    tag: str
    recipient: Recipient


@dataclass(frozen=True, slots=True)
class ScheduleSlot:
    index: int
    scheduled_at: datetime
    target_count: int
