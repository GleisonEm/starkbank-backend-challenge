from dataclasses import dataclass

from starkbank_trial.application.clock import Clock
from starkbank_trial.application.payers import build_smoke_invoice
from starkbank_trial.domain.invoices import ProviderInvoice
from starkbank_trial.domain.provider import InvoiceProvider, ProviderUnknownOutcomeError
from starkbank_trial.persistence.invoice_store import InvoiceStore


@dataclass(frozen=True, slots=True)
class SmokeBatchResult:
    invoices: tuple[ProviderInvoice, ...]
    reused: int


@dataclass(frozen=True, slots=True)
class SmokeBatchService:
    provider: InvoiceProvider
    clock: Clock
    invoice_store: InvoiceStore
    namespace: str

    def run(self, reference: str, count: int, amount: int) -> SmokeBatchResult:
        invoices: list[ProviderInvoice] = []
        reused = 0
        for ordinal in range(count):
            now = self.clock.now()
            draft = build_smoke_invoice(
                f"{reference}:{ordinal}",
                amount,
                now,
                namespace=self.namespace,
            )
            self.invoice_store.register_smoke(draft, now)
            existing = self.provider.find_invoice(draft.tag)
            if existing is not None:
                invoices.append(existing)
                reused += 1
                self.invoice_store.record_smoke(draft, str(existing.id), now)
                continue
            try:
                created = self.provider.create_invoice(draft)
            except ProviderUnknownOutcomeError:
                reconciled = self.provider.find_invoice(draft.tag)
                if reconciled is None:
                    raise
                created = reconciled
            invoices.append(created)
            self.invoice_store.record_smoke(draft, str(created.id), self.clock.now())
        return SmokeBatchResult(tuple(invoices), reused)
