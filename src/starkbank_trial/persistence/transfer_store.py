from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine, and_, or_, select, update

from starkbank_trial.domain.jobs import JobClaim, TransferJob
from starkbank_trial.domain.provider import ProviderTransfer
from starkbank_trial.domain.status import TransferStatus
from starkbank_trial.domain.types import (
    Cents,
    EventId,
    ExternalId,
    InvoiceId,
    TransferJobId,
)
from starkbank_trial.persistence.schema import transfer_jobs


@dataclass(frozen=True, slots=True)
class _JobFinish:
    status: TransferStatus
    now: datetime
    provider_transfer_id: str | None
    error_code: str | None
    next_attempt_at: datetime
    provider_status: str | None = None
    provider_log_type: str | None = None
    provider_status_updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TransferSyncCandidate:
    id: TransferJobId
    invoice_id: InvoiceId
    net_amount: Cents


@dataclass(frozen=True, slots=True)
class TransferStore:
    engine: Engine

    def claim(self, claim: JobClaim) -> TransferJob | None:
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    select(transfer_jobs)
                    .where(
                        or_(
                            and_(
                                transfer_jobs.c.status.in_(
                                    (TransferStatus.PENDING, TransferStatus.UNKNOWN)
                                ),
                                transfer_jobs.c.next_attempt_at <= claim.now,
                            ),
                            and_(
                                transfer_jobs.c.status == TransferStatus.PROCESSING,
                                transfer_jobs.c.lease_until <= claim.now,
                            ),
                        )
                    )
                    .order_by(transfer_jobs.c.next_attempt_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            attempts = int(row["attempts"]) + 1
            connection.execute(
                update(transfer_jobs)
                .where(transfer_jobs.c.id == row["id"])
                .values(
                    status=TransferStatus.PROCESSING,
                    attempts=attempts,
                    lease_until=claim.lease_until,
                    updated_at=claim.now,
                )
            )
        return TransferJob(
            id=TransferJobId(str(row["id"])),
            event_id=EventId(str(row["event_id"])),
            invoice_id=InvoiceId(str(row["invoice_id"])),
            amount=Cents(int(row["amount"])),
            fee=Cents(int(row["fee"])),
            net_amount=Cents(int(row["net_amount"])),
            external_id=ExternalId(str(row["external_id"])),
            tag=str(row["tag"]),
            status=TransferStatus.PROCESSING,
            attempts=attempts,
            lease_until=claim.lease_until,
            claimed_from=TransferStatus(str(row["status"])),
        )

    def sync_candidates(self, limit: int = 100) -> tuple[TransferSyncCandidate, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(transfer_jobs)
                .where(
                    transfer_jobs.c.status == TransferStatus.SUCCEEDED,
                    transfer_jobs.c.provider_status.is_(None),
                )
                .order_by(transfer_jobs.c.created_at)
                .limit(limit)
            ).mappings()
            return tuple(
                TransferSyncCandidate(
                    id=TransferJobId(str(row["id"])),
                    invoice_id=InvoiceId(str(row["invoice_id"])),
                    net_amount=Cents(int(row["net_amount"])),
                )
                for row in rows
            )

    def refresh_provider_status(
        self,
        job_id: TransferJobId,
        transfer: ProviderTransfer,
        now: datetime,
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(transfer_jobs)
                .where(transfer_jobs.c.id == job_id)
                .values(
                    provider_transfer_id=transfer.id,
                    provider_status=transfer.status,
                    provider_status_updated_at=now,
                    updated_at=now,
                )
            )

    def succeeded(
        self,
        job: TransferJob,
        transfer: ProviderTransfer,
        now: datetime,
    ) -> bool:
        return self._finish(
            job,
            _JobFinish(
                status=TransferStatus.SUCCEEDED,
                now=now,
                provider_transfer_id=transfer.id,
                error_code=None,
                next_attempt_at=now,
                provider_status=transfer.status,
                provider_status_updated_at=now,
            ),
        )

    def retry(
        self,
        job: TransferJob,
        error_code: str,
        now: datetime,
        delay: timedelta,
    ) -> bool:
        return self._finish(
            job,
            _JobFinish(
                status=TransferStatus.PENDING,
                now=now,
                provider_transfer_id=None,
                error_code=error_code,
                next_attempt_at=now + delay,
            ),
        )

    def unknown(
        self,
        job: TransferJob,
        error_code: str,
        now: datetime,
        delay: timedelta,
    ) -> bool:
        return self._finish(
            job,
            _JobFinish(
                status=TransferStatus.UNKNOWN,
                now=now,
                provider_transfer_id=None,
                error_code=error_code,
                next_attempt_at=now + delay,
            ),
        )

    def failed(self, job: TransferJob, error_code: str, now: datetime) -> bool:
        return self._finish(
            job,
            _JobFinish(
                status=TransferStatus.PERMANENT_FAILURE,
                now=now,
                provider_transfer_id=None,
                error_code=error_code,
                next_attempt_at=now,
            ),
        )

    def _finish(self, job: TransferJob, finish: _JobFinish) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(
                update(transfer_jobs)
                .where(
                    transfer_jobs.c.id == job.id,
                    transfer_jobs.c.status == TransferStatus.PROCESSING,
                    transfer_jobs.c.lease_until == job.lease_until,
                )
                .values(
                    status=finish.status,
                    next_attempt_at=finish.next_attempt_at,
                    lease_until=None,
                    provider_transfer_id=finish.provider_transfer_id,
                    provider_status=finish.provider_status,
                    provider_log_type=finish.provider_log_type,
                    provider_status_updated_at=finish.provider_status_updated_at,
                    last_error_code=finish.error_code,
                    updated_at=finish.now,
                )
            )
        return result.rowcount == 1
