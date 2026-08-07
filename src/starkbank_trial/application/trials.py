from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum, unique
from secrets import randbelow
from typing import TYPE_CHECKING, Protocol, assert_never

from starkbank_trial.application.clock import Clock
from starkbank_trial.application.payers import build_invoice_drafts
from starkbank_trial.application.schedule import build_schedule
from starkbank_trial.domain.errors import LiveOperationsDisabledError
from starkbank_trial.domain.invoices import (
    DraftCreated,
    DraftFailure,
    InvoiceDraft,
    InvoiceReconciliation,
)
from starkbank_trial.domain.provider import (
    InvoiceProvider,
    ProviderPermanentError,
    ProviderTimeoutError,
    ProviderTransientError,
)
from starkbank_trial.domain.status import BatchStatus
from starkbank_trial.domain.trials import BatchClaim, NewTrial, TrialRun
from starkbank_trial.persistence.stores import Stores

if TYPE_CHECKING:
    from starkbank_trial.domain.types import BatchId


class BatchCounts(Protocol):
    def next(self) -> tuple[int, ...]: ...


class SecureBatchCounts:
    def next(self) -> tuple[int, ...]:
        return tuple(randbelow(5) + 8 for _ in range(8))


@unique
class TrialTickResult(StrEnum):
    NO_BATCH = "no_batch"
    COMPLETED = "completed"
    RECONCILING = "reconciling"
    DEGRADED = "degraded"
    RETRY_PENDING = "retry_pending"
    LIVE_DISABLED = "live_disabled"


@dataclass(frozen=True, slots=True)
class TrialService:
    stores: Stores
    provider: InvoiceProvider
    clock: Clock
    batch_counts: BatchCounts
    batch_lease_seconds: int = 300
    live_operations_enabled: bool = True
    retry_base_seconds: int = 5
    invoice_max_attempts: int = 5
    invoice_reconciliation_max_attempts: int = 5
    batch_max_attempts: int = 15

    def start(self, start_at: datetime | None = None) -> TrialRun:
        if not self.live_operations_enabled:
            raise LiveOperationsDisabledError(operation="trial_start")
        effective_start = start_at if start_at is not None else self.clock.now()
        schedule = build_schedule(effective_start, self.batch_counts.next())
        return self.stores.trials.create(NewTrial(effective_start, schedule))

    def tick(self) -> TrialTickResult:
        if not self.live_operations_enabled:
            return TrialTickResult.LIVE_DISABLED
        now = self.clock.now()
        batch = self.stores.trials.claim_due_batch(
            BatchClaim(
                now=now,
                lease_until=now + timedelta(seconds=self.batch_lease_seconds),
                max_attempts=self.batch_max_attempts,
            )
        )
        if batch is None:
            return TrialTickResult.NO_BATCH
        self.stores.invoices.save(batch.id, build_invoice_drafts(batch, now))
        for draft in self.stores.invoices.pending(batch.id, now):
            self._issue_draft(draft, now)
        return self._tick_result(self.stores.invoices.settle(batch.id, now))

    def _issue_draft(self, draft: InvoiceDraft, now: datetime) -> None:
        if draft.attempts > 0 and self._settle_attempted_draft(draft, now):
            return
        try:
            invoice = self.provider.create_invoice(draft)
        except ProviderTimeoutError as error:
            self.stores.invoices.unknown_result(DraftFailure(draft.id, type(error).__name__, now))
        except ProviderTransientError as error:
            self.stores.invoices.retry(
                DraftFailure(draft.id, type(error).__name__, now),
                self._backoff(draft.attempts),
                self.invoice_max_attempts,
            )
        except ProviderPermanentError as error:
            self.stores.invoices.failed(DraftFailure(draft.id, type(error).__name__, now))
        else:
            self.stores.invoices.created(DraftCreated(draft.id, invoice.id, now))

    def _settle_attempted_draft(self, draft: InvoiceDraft, now: datetime) -> bool:
        try:
            existing = self.provider.find_invoice(draft.tag)
        except ProviderTransientError as error:
            self.stores.invoices.retry(
                DraftFailure(draft.id, type(error).__name__, now),
                self._backoff(draft.attempts),
                self.invoice_max_attempts,
            )
            return True
        except ProviderPermanentError as error:
            self.stores.invoices.failed(DraftFailure(draft.id, type(error).__name__, now))
            return True
        if existing is not None:
            self.stores.invoices.created(DraftCreated(draft.id, existing.id, now))
            return True
        return False

    def reconcile_invoices(self) -> int:
        now = self.clock.now()
        reconciled = 0
        batches: set[BatchId] = set()
        for draft in self.stores.invoices.unknown(now):
            try:
                invoice = self.provider.find_invoice(draft.tag)
            except ProviderTransientError:
                continue
            self.stores.invoices.reconcile(
                InvoiceReconciliation(draft, invoice, now),
                max_attempts=self.invoice_max_attempts,
                max_reconciliation_attempts=self.invoice_reconciliation_max_attempts,
                retry_delay=self._backoff(draft.attempts),
            )
            batches.add(draft.batch_id)
            if invoice is not None:
                reconciled += 1
        for batch_id in batches:
            self.stores.invoices.settle(batch_id, now)
        return reconciled

    def _backoff(self, attempts: int) -> timedelta:
        return timedelta(seconds=self.retry_base_seconds * (2 ** min(attempts, 8)))

    @staticmethod
    def _tick_result(status: BatchStatus) -> TrialTickResult:
        match status:
            case BatchStatus.COMPLETED:
                return TrialTickResult.COMPLETED
            case BatchStatus.RECONCILING:
                return TrialTickResult.RECONCILING
            case BatchStatus.DEGRADED:
                return TrialTickResult.DEGRADED
            case BatchStatus.SCHEDULED | BatchStatus.ISSUING:
                return TrialTickResult.RETRY_PENDING
        return assert_never(status)
