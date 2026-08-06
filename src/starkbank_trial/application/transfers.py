from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum, unique

import structlog

from starkbank_trial.application.clock import Clock
from starkbank_trial.domain.jobs import JobClaim, TransferJob
from starkbank_trial.domain.provider import (
    ProviderPermanentError,
    ProviderTimeoutError,
    ProviderTransientError,
    TransferProvider,
)
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


@dataclass(frozen=True, slots=True)
class TransferWorker:
    store: TransferStore
    provider: TransferProvider
    clock: Clock
    lease_seconds: int = 120
    retry_base_seconds: int = 5

    def process_one(self) -> WorkerResult:
        now = self.clock.now()
        job = self.store.claim(
            JobClaim(now=now, lease_until=now + timedelta(seconds=self.lease_seconds))
        )
        if job is None:
            return WorkerResult.IDLE
        command = build_transfer_command(job.invoice_id, job.net_amount)
        try:
            transfer = self.provider.ensure_transfer(command)
        except ProviderTimeoutError as error:
            self.store.unknown(job, type(error).__name__, now, self._backoff(job))
            logger.warning(
                "transfer_reconciliation_scheduled",
                invoice_id=job.invoice_id,
                attempt=job.attempts,
            )
            return WorkerResult.RECONCILIATION_SCHEDULED
        except ProviderTransientError as error:
            self.store.retry(job, type(error).__name__, now, self._backoff(job))
            logger.warning(
                "transfer_retry_scheduled",
                invoice_id=job.invoice_id,
                attempt=job.attempts,
            )
            return WorkerResult.RETRY_SCHEDULED
        except ProviderPermanentError as error:
            self.store.failed(job, type(error).__name__, now)
            logger.error(  # noqa: TRY400 - provider exceptions may contain financial data
                "transfer_failed",
                invoice_id=job.invoice_id,
                attempt=job.attempts,
            )
            return WorkerResult.PERMANENT_FAILURE
        self.store.succeeded(job, transfer, now)
        logger.info(
            "transfer_succeeded",
            invoice_id=job.invoice_id,
            provider_transfer_id=transfer.id,
        )
        return WorkerResult.SUCCEEDED

    def _backoff(self, job: TransferJob) -> timedelta:
        exponent = min(job.attempts - 1, 8)
        return timedelta(seconds=self.retry_base_seconds * (2**exponent))
