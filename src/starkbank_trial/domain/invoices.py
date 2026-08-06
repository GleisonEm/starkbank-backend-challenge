from dataclasses import dataclass
from datetime import datetime

from starkbank_trial.domain.status import DraftStatus
from starkbank_trial.domain.types import BatchId, Cents, DraftId, InvoiceId


@dataclass(frozen=True, slots=True)
class InvoiceDraft:
    id: DraftId
    batch_id: BatchId
    ordinal: int
    payer_name: str
    payer_tax_id: str
    amount: Cents
    tag: str
    status: DraftStatus
    attempts: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderInvoice:
    id: InvoiceId
    tag: str


@dataclass(frozen=True, slots=True)
class DraftCreated:
    draft_id: DraftId
    invoice_id: InvoiceId
    at: datetime


@dataclass(frozen=True, slots=True)
class DraftFailure:
    draft_id: DraftId
    error_code: str
    at: datetime


@dataclass(frozen=True, slots=True)
class InvoiceReconciliation:
    draft: InvoiceDraft
    provider_invoice: ProviderInvoice | None
    at: datetime
