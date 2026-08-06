from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Engine, func, insert, select, update
from sqlalchemy.engine import Connection, RowMapping

from starkbank_trial.domain.invoices import (
    DraftCreated,
    DraftFailure,
    InvoiceDraft,
    InvoiceReconciliation,
)
from starkbank_trial.domain.status import BatchStatus, DraftStatus, TrialStatus
from starkbank_trial.domain.types import BatchId, Cents, DraftId
from starkbank_trial.persistence.schema import invoice_batches, invoice_drafts, trial_runs

MAX_RECONCILIATION_ATTEMPTS = 3


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _draft(row: RowMapping) -> InvoiceDraft:
    return InvoiceDraft(
        id=DraftId(str(row["id"])),
        batch_id=BatchId(str(row["batch_id"])),
        ordinal=int(row["ordinal"]),
        payer_name=str(row["payer_name"]),
        payer_tax_id=str(row["payer_tax_id"]),
        amount=Cents(int(row["amount"])),
        tag=str(row["tag"]),
        status=DraftStatus(str(row["status"])),
        attempts=int(row["attempts"]),
        created_at=_as_utc(row["created_at"]),
    )


@dataclass(frozen=True, slots=True)
class InvoiceStore:
    engine: Engine

    def save(self, batch_id: BatchId, drafts: tuple[InvoiceDraft, ...]) -> None:
        with self.engine.begin() as connection:
            existing = set(
                connection.execute(
                    select(invoice_drafts.c.id).where(invoice_drafts.c.batch_id == batch_id)
                ).scalars()
            )
            rows = [
                {
                    "id": draft.id,
                    "batch_id": draft.batch_id,
                    "ordinal": draft.ordinal,
                    "payer_name": draft.payer_name,
                    "payer_tax_id": draft.payer_tax_id,
                    "amount": draft.amount,
                    "tag": draft.tag,
                    "status": draft.status,
                    "attempts": draft.attempts,
                    "reconcile_attempts": 0,
                    "created_at": draft.created_at,
                    "updated_at": draft.created_at,
                }
                for draft in drafts
                if draft.id not in existing
            ]
            if rows:
                connection.execute(insert(invoice_drafts), rows)

    def pending(self, batch_id: BatchId) -> tuple[InvoiceDraft, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(invoice_drafts)
                .where(
                    invoice_drafts.c.batch_id == batch_id,
                    invoice_drafts.c.status == DraftStatus.PENDING,
                )
                .order_by(invoice_drafts.c.ordinal)
            ).mappings()
            return tuple(_draft(row) for row in rows)

    def unknown(self, limit: int = 100) -> tuple[InvoiceDraft, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(invoice_drafts)
                .where(invoice_drafts.c.status == DraftStatus.UNKNOWN)
                .order_by(invoice_drafts.c.updated_at)
                .limit(limit)
            ).mappings()
            return tuple(_draft(row) for row in rows)

    def created(self, result: DraftCreated) -> None:
        self._transition(
            result.draft_id,
            DraftStatus.CREATED,
            result.at,
            provider_invoice_id=result.invoice_id,
            error_code=None,
        )

    def unknown_result(self, result: DraftFailure) -> None:
        self._transition(
            result.draft_id,
            DraftStatus.UNKNOWN,
            result.at,
            provider_invoice_id=None,
            error_code=result.error_code,
        )

    def retry(self, result: DraftFailure) -> None:
        self._transition(
            result.draft_id,
            DraftStatus.PENDING,
            result.at,
            provider_invoice_id=None,
            error_code=result.error_code,
        )

    def failed(self, result: DraftFailure) -> None:
        self._transition(
            result.draft_id,
            DraftStatus.FAILED,
            result.at,
            provider_invoice_id=None,
            error_code=result.error_code,
        )

    def reconcile(self, result: InvoiceReconciliation) -> None:
        if result.provider_invoice is not None:
            self.created(DraftCreated(result.draft.id, result.provider_invoice.id, result.at))
            return
        with self.engine.begin() as connection:
            attempts = connection.execute(
                select(invoice_drafts.c.reconcile_attempts).where(
                    invoice_drafts.c.id == result.draft.id
                )
            ).scalar_one()
            next_attempts = int(attempts) + 1
            next_status = (
                DraftStatus.PENDING
                if next_attempts >= MAX_RECONCILIATION_ATTEMPTS
                else DraftStatus.UNKNOWN
            )
            connection.execute(
                update(invoice_drafts)
                .where(invoice_drafts.c.id == result.draft.id)
                .values(
                    status=next_status,
                    reconcile_attempts=next_attempts,
                    updated_at=result.at,
                )
            )

    def settle(self, batch_id: BatchId, now: datetime) -> BatchStatus:
        with self.engine.begin() as connection:
            batch = (
                connection.execute(select(invoice_batches).where(invoice_batches.c.id == batch_id))
                .mappings()
                .one()
            )
            counts: dict[DraftStatus, int] = {
                DraftStatus(str(row[0])): int(row[1])
                for row in connection.execute(
                    select(invoice_drafts.c.status, func.count())
                    .where(invoice_drafts.c.batch_id == batch_id)
                    .group_by(invoice_drafts.c.status)
                )
            }
            if counts.get(DraftStatus.CREATED, 0) == int(batch["target_count"]):
                status = BatchStatus.COMPLETED
            elif counts.get(DraftStatus.FAILED, 0) > 0:
                status = BatchStatus.DEGRADED
            elif counts.get(DraftStatus.UNKNOWN, 0) > 0:
                status = BatchStatus.RECONCILING
            else:
                status = BatchStatus.SCHEDULED
            connection.execute(
                update(invoice_batches)
                .where(invoice_batches.c.id == batch_id)
                .values(
                    status=status,
                    lease_until=None,
                    completed_at=now if status is BatchStatus.COMPLETED else None,
                )
            )
            self._settle_run(connection, str(batch["run_id"]), status, now)
        return status

    def _transition(
        self,
        draft_id: DraftId,
        status: DraftStatus,
        at: datetime,
        *,
        provider_invoice_id: str | None,
        error_code: str | None,
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(invoice_drafts)
                .where(invoice_drafts.c.id == draft_id)
                .values(
                    status=status,
                    provider_invoice_id=provider_invoice_id,
                    attempts=invoice_drafts.c.attempts + 1,
                    last_error_code=error_code,
                    updated_at=at,
                )
            )

    @staticmethod
    def _settle_run(
        connection: Connection,
        run_id: str,
        status: BatchStatus,
        now: datetime,
    ) -> None:
        if status is BatchStatus.DEGRADED:
            connection.execute(
                update(trial_runs)
                .where(trial_runs.c.id == run_id)
                .values(status=TrialStatus.DEGRADED, active_marker=None)
            )
            return
        remaining = connection.execute(
            select(func.count())
            .select_from(invoice_batches)
            .where(
                invoice_batches.c.run_id == run_id,
                invoice_batches.c.status != BatchStatus.COMPLETED,
            )
        ).scalar_one()
        if remaining == 0:
            connection.execute(
                update(trial_runs)
                .where(trial_runs.c.id == run_id)
                .values(
                    status=TrialStatus.COMPLETED,
                    active_marker=None,
                    completed_at=now,
                )
            )
