from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import Engine, func, insert, or_, select, update
from sqlalchemy.engine import Connection

from starkbank_trial.domain.constants import SMOKE_RUN_ID
from starkbank_trial.domain.status import BatchStatus, TrialStatus
from starkbank_trial.domain.trials import (
    BatchClaim,
    InvoiceBatch,
    NewTrial,
    StatusCount,
    TrialReport,
    TrialRun,
    TrialSummary,
)
from starkbank_trial.domain.types import BatchId, TrialRunId
from starkbank_trial.persistence.schema import (
    invoice_batches,
    invoice_drafts,
    transfer_jobs,
    trial_runs,
    webhook_events,
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class TrialStore:
    engine: Engine

    def report(self) -> TrialReport:
        with self.engine.connect() as connection:
            trial = (
                connection.execute(
                    select(trial_runs)
                    .where(trial_runs.c.id != SMOKE_RUN_ID)
                    .order_by(trial_runs.c.created_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            event_counts = tuple(
                StatusCount(str(row[0]), int(row[1]))
                for row in connection.execute(
                    select(webhook_events.c.outcome, func.count())
                    .group_by(webhook_events.c.outcome)
                    .order_by(webhook_events.c.outcome)
                )
            )
            transfer_counts = tuple(
                StatusCount(str(row[0]), int(row[1]))
                for row in connection.execute(
                    select(transfer_jobs.c.status, func.count())
                    .group_by(transfer_jobs.c.status)
                    .order_by(transfer_jobs.c.status)
                )
            )
            if trial is None:
                return TrialReport(None, (), (), event_counts, transfer_counts)
            run_id = str(trial["id"])
            batch_counts = tuple(
                StatusCount(str(row[0]), int(row[1]))
                for row in connection.execute(
                    select(invoice_batches.c.status, func.count())
                    .where(invoice_batches.c.run_id == run_id)
                    .group_by(invoice_batches.c.status)
                    .order_by(invoice_batches.c.status)
                )
            )
            invoice_counts = tuple(
                StatusCount(str(row[0]), int(row[1]))
                for row in connection.execute(
                    select(invoice_drafts.c.status, func.count())
                    .select_from(
                        invoice_drafts.join(
                            invoice_batches,
                            invoice_drafts.c.batch_id == invoice_batches.c.id,
                        )
                    )
                    .where(invoice_batches.c.run_id == run_id)
                    .group_by(invoice_drafts.c.status)
                    .order_by(invoice_drafts.c.status)
                )
            )
            next_batch = connection.execute(
                select(func.min(invoice_batches.c.scheduled_at)).where(
                    invoice_batches.c.run_id == run_id,
                    invoice_batches.c.status.not_in((BatchStatus.COMPLETED, BatchStatus.DEGRADED)),
                )
            ).scalar_one()
        summary = TrialSummary(
            id=TrialRunId(run_id),
            status=TrialStatus(str(trial["status"])),
            started_at=_utc(trial["started_at"]),
            ends_at=_utc(trial["ends_at"]),
            next_batch_at=_utc(next_batch) if next_batch is not None else None,
        )
        return TrialReport(summary, batch_counts, invoice_counts, event_counts, transfer_counts)

    def create(self, command: NewTrial) -> TrialRun:
        run_id = TrialRunId(str(uuid4()))
        ends_at = command.start_at + timedelta(hours=24)
        with self.engine.begin() as connection:
            connection.execute(
                insert(trial_runs).values(
                    id=run_id,
                    status=TrialStatus.ACTIVE,
                    started_at=command.start_at,
                    ends_at=ends_at,
                    created_at=command.start_at,
                    active_marker=1,
                )
            )
            connection.execute(
                insert(invoice_batches),
                [
                    {
                        "id": str(uuid5(NAMESPACE_URL, f"{run_id}:batch:{slot.index}")),
                        "run_id": run_id,
                        "slot_index": slot.index,
                        "scheduled_at": slot.scheduled_at,
                        "target_count": slot.target_count,
                        "status": BatchStatus.SCHEDULED,
                        "attempts": 0,
                        "created_at": command.start_at,
                    }
                    for slot in command.schedule
                ],
            )
        return TrialRun(run_id, TrialStatus.ACTIVE, command.start_at, ends_at)

    def claim_due_batch(self, claim: BatchClaim) -> InvoiceBatch | None:
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    select(invoice_batches)
                    .where(
                        invoice_batches.c.scheduled_at <= claim.now,
                        invoice_batches.c.status.in_(
                            (BatchStatus.SCHEDULED, BatchStatus.ISSUING, BatchStatus.RECONCILING)
                        ),
                        or_(
                            invoice_batches.c.lease_until.is_(None),
                            invoice_batches.c.lease_until <= claim.now,
                        ),
                        invoice_batches.c.attempts < claim.max_attempts,
                    )
                    .order_by(invoice_batches.c.scheduled_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if row is None:
                self._degrade_exhausted_batches(connection, claim)
                return None
            attempts = int(row["attempts"]) + 1
            connection.execute(
                update(invoice_batches)
                .where(invoice_batches.c.id == row["id"])
                .values(
                    status=BatchStatus.ISSUING,
                    lease_until=claim.lease_until,
                    attempts=attempts,
                )
            )
        return InvoiceBatch(
            id=BatchId(str(row["id"])),
            run_id=TrialRunId(str(row["run_id"])),
            slot_index=int(row["slot_index"]),
            scheduled_at=_utc(row["scheduled_at"]),
            target_count=int(row["target_count"]),
            status=BatchStatus.ISSUING,
            lease_until=claim.lease_until,
            attempts=attempts,
        )

    @staticmethod
    def _degrade_exhausted_batches(connection: Connection, claim: BatchClaim) -> None:
        exhausted = connection.execute(
            update(invoice_batches)
            .where(
                invoice_batches.c.scheduled_at <= claim.now,
                invoice_batches.c.status.in_(
                    (BatchStatus.SCHEDULED, BatchStatus.ISSUING, BatchStatus.RECONCILING)
                ),
                or_(
                    invoice_batches.c.lease_until.is_(None),
                    invoice_batches.c.lease_until <= claim.now,
                ),
                invoice_batches.c.attempts >= claim.max_attempts,
            )
            .values(
                status=BatchStatus.DEGRADED,
                lease_until=None,
                completed_at=claim.now,
            )
        )
        if exhausted.rowcount == 0:
            return
        run_ids = connection.execute(
            select(invoice_batches.c.run_id)
            .where(invoice_batches.c.status == BatchStatus.DEGRADED)
            .distinct()
        ).scalars()
        for run_id in run_ids:
            connection.execute(
                update(trial_runs)
                .where(trial_runs.c.id == run_id)
                .values(status=TrialStatus.DEGRADED, active_marker=None)
            )
