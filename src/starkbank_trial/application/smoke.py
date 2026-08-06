from dataclasses import dataclass

from starkbank_trial.application.clock import Clock
from starkbank_trial.application.payers import build_smoke_invoice
from starkbank_trial.domain.invoices import ProviderInvoice
from starkbank_trial.domain.provider import InvoiceProvider, ProviderUnknownOutcomeError


@dataclass(frozen=True, slots=True)
class SmokeBatchResult:
    invoices: tuple[ProviderInvoice, ...]
    reused: int


@dataclass(frozen=True, slots=True)
class SmokeBatchService:
    provider: InvoiceProvider
    clock: Clock

    def run(self, reference: str, count: int, amount: int) -> SmokeBatchResult:
        invoices: list[ProviderInvoice] = []
        reused = 0
        for ordinal in range(count):
            draft = build_smoke_invoice(f"{reference}:{ordinal}", amount, self.clock.now())
            existing = self.provider.find_invoice(draft.tag)
            if existing is not None:
                invoices.append(existing)
                reused += 1
                continue
            try:
                invoices.append(self.provider.create_invoice(draft))
            except ProviderUnknownOutcomeError:
                reconciled = self.provider.find_invoice(draft.tag)
                if reconciled is None:
                    raise
                invoices.append(reconciled)
        return SmokeBatchResult(tuple(invoices), reused)
