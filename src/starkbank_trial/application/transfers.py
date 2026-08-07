from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum, unique

import structlog

from starkbank_trial.application.clock import Clock
from starkbank_trial.domain.jobs import JobClaim, TransferJob
from starkbank_trial.domain.models import TransferCommand
from starkbank_trial.domain.provider import (
    ProviderPermanentError,
    ProviderTimeoutError,
    ProviderTransientError,
    TransferProvider,
)
from starkbank_trial.domain.status import TransferStatus
from starkbank_trial.domain.transfer import build_transfer_command
from starkbank_trial.persistence.transfer_store import TransferStore

logger = structlog.get_logger()


@unique
class WorkerResult(StrEnum):
    IDLE = "idle"
    SUCCEEDED = "succeeded"
    RETRY_SCHEDULED = "retry_scheduled"
    RECONCILIATION_SCHEDULED = "reconciliation_scheduled"
    PERMANENT_FAILURE = "permanent_failure"
    LIVE_DISABLED = "live_disabled"


def should_poll(result: WorkerResult) -> bool:
    """Whether the worker loop should sleep after a result (no work left to do)."""
    return result in (WorkerResult.IDLE, WorkerResult.LIVE_DISABLED)


@dataclass(frozen=True, slots=True)
class TransferWorker:
    store: TransferStore
    provider: TransferProvider
    clock: Clock
    lease_seconds: int = 120
    retry_base_seconds: int = 5
    transfer_max_attempts: int = 10
    live_operations_enabled: bool = True

    def process_one(self) -> WorkerResult:
        if not self.live_operations_enabled:
            return WorkerResult.LIVE_DISABLED
        job = self._claim()
        if job is None:
            return WorkerResult.IDLE
        return self._process_claimed(job, self.clock.now())

    def _claim(self) -> TransferJob | None:
        now = self.clock.now()
        return self.store.claim(
            JobClaim(now=now, lease_until=now + timedelta(seconds=self.lease_seconds))
        )

    def _process_claimed(self, job: TransferJob, now: datetime) -> WorkerResult:
        command = build_transfer_command(job.invoice_id, job.net_amount)
        if job.claimed_from is TransferStatus.UNKNOWN:
            return self._reconcile_unknown(job, command, now)
        try:
            transfer = self.provider.ensure_transfer(command)
        except ProviderTimeoutError as error:
            return self._handle_timeout(job, command, now, error)
        except ProviderTransientError as error:
            return self._handle_transient(job, command, now, error)
        except ProviderPermanentError as error:
            return self._handle_permanent(job, now, error)
        self.store.succeeded(job, transfer, now)
        logger.info(
            "transfer_succeeded",
            invoice_id=job.invoice_id,
            provider_transfer_id=transfer.id,
        )
        return WorkerResult.SUCCEEDED

    def _handle_timeout(
        self,
        job: TransferJob,
        command: TransferCommand,
        now: datetime,
        error: ProviderTimeoutError,
    ) -> WorkerResult:
        if job.attempts >= self.transfer_max_attempts:
            return self._finalize_after_lookup(job, command, now)
        self.store.unknown(job, type(error).__name__, now, self._backoff(job))
        logger.warning(
            "transfer_reconciliation_scheduled",
            invoice_id=job.invoice_id,
            attempt=job.attempts,
        )
        return WorkerResult.RECONCILIATION_SCHEDULED

    def _handle_transient(
        self,
        job: TransferJob,
        command: TransferCommand,
        now: datetime,
        error: ProviderTransientError,
    ) -> WorkerResult:
        if job.attempts >= self.transfer_max_attempts:
            return self._finalize_after_lookup(job, command, now)
        self.store.retry(job, type(error).__name__, now, self._backoff(job))
        logger.warning(
            "transfer_retry_scheduled",
            invoice_id=job.invoice_id,
            attempt=job.attempts,
        )
        return WorkerResult.RETRY_SCHEDULED

    def _handle_permanent(
        self,
        job: TransferJob,
        now: datetime,
        error: ProviderPermanentError,
    ) -> WorkerResult:
        self.store.failed(job, type(error).__name__, now)
        logger.error(
            "transfer_failed",
            invoice_id=job.invoice_id,
            attempt=job.attempts,
        )
        return WorkerResult.PERMANENT_FAILURE

    def _finalize_after_lookup(
        self,
        job: TransferJob,
        command: TransferCommand,
        now: datetime,
    ) -> WorkerResult:
        try:
            transfer = self.provider.find_transfer(command)
        except ProviderTransientError:
            transfer = None
        if transfer is not None:
            self.store.succeeded(job, transfer, now)
            return WorkerResult.SUCCEEDED
        self.store.failed(job, "retry_exhausted", now)
        return WorkerResult.PERMANENT_FAILURE

    def _reconcile_unknown(
        self,
        job: TransferJob,
        command: TransferCommand,
        now: datetime,
    ) -> WorkerResult:
        try:
            transfer = self.provider.find_transfer(command)
        except ProviderTransientError as error:
            if job.attempts >= self.transfer_max_attempts:
                self.store.failed(job, "retry_exhausted", now)
                return WorkerResult.PERMANENT_FAILURE
            self.store.unknown(job, type(error).__name__, now, self._backoff(job))
            return WorkerResult.RECONCILIATION_SCHEDULED
        except ProviderPermanentError as error:
            self.store.failed(job, type(error).__name__, now)
            return WorkerResult.PERMANENT_FAILURE
        if transfer is not None:
            self.store.succeeded(job, transfer, now)
            return WorkerResult.SUCCEEDED
        if job.attempts >= self.transfer_max_attempts:
            self.store.failed(job, "retry_exhausted", now)
            return WorkerResult.PERMANENT_FAILURE
        self.store.retry(job, "reconciliation_not_found", now, self._backoff(job))
        return WorkerResult.RETRY_SCHEDULED

    def _backoff(self, job: TransferJob) -> timedelta:
        exponent = min(job.attempts - 1, 8)
        return timedelta(seconds=self.retry_base_seconds * (2**exponent))
