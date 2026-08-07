from dataclasses import dataclass
from typing import Protocol

from starkbank_trial.domain.events import VerifiedEvent
from starkbank_trial.domain.invoices import InvoiceDraft, ProviderInvoice
from starkbank_trial.domain.models import TransferCommand
from starkbank_trial.domain.types import ExternalId, TransferId


@dataclass(slots=True)
class ProviderError(Exception):
    operation: str

    def __str__(self) -> str:
        return f"provider operation failed: {self.operation}"


@dataclass(slots=True)
class ProviderTransientError(ProviderError):
    pass


@dataclass(slots=True)
class ProviderTimeoutError(ProviderTransientError):
    pass


@dataclass(slots=True)
class ProviderUnknownOutcomeError(ProviderTimeoutError):
    pass


@dataclass(slots=True)
class ProviderPermanentError(ProviderError):
    pass


@dataclass(slots=True)
class InvalidWebhookError(ProviderError):
    pass


@dataclass(slots=True)
class UnexpectedWorkspaceError(ProviderError):
    workspace_id: str
    event_id: str
    subscription: str
    log_type: str

    def __str__(self) -> str:
        return f"provider event belongs to an unexpected workspace: {self.operation}"


class InvoiceProvider(Protocol):
    def create_invoice(self, draft: InvoiceDraft) -> ProviderInvoice: ...

    def find_invoice(self, tag: str) -> ProviderInvoice | None: ...


@dataclass(frozen=True, slots=True)
class ProviderTransfer:
    id: TransferId
    external_id: ExternalId
    status: str


@dataclass(frozen=True, slots=True)
class ProviderWebhook:
    id: str
    url: str
    subscriptions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WebhookInspection:
    active: ProviderWebhook | None
    stale: tuple[ProviderWebhook, ...]


class TransferProvider(Protocol):
    def ensure_transfer(self, command: TransferCommand) -> ProviderTransfer: ...

    def find_transfer(self, command: TransferCommand) -> ProviderTransfer | None: ...


class WebhookVerifier(Protocol):
    def verify_event(self, content: bytes, signature: str) -> VerifiedEvent: ...
