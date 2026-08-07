from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, RowMapping

from starkbank_trial.domain.constants import SMOKE_BATCH_ID, SMOKE_RUN_ID
from starkbank_trial.domain.invoices import (
    DraftCreated,
    DraftFailure,
    InvoiceDraft,
    InvoiceReconciliation,
)
from starkbank_trial.domain.status import BatchStatus, DraftStatus, TrialStatus
from starkbank_trial.domain.types import BatchId, Cents, DraftId
from starkbank_trial.persistence.schema import invoice_batches, invoice_drafts, trial_runs

MAX_RECONCILIATION_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class _DraftTransition:
    draft_id: DraftId
    status: DraftStatus
    at: datetime
    provider_invoice_id: str | None
    error_code: str | None
    next_attempt_at: datetime
    reset_reconcile_attempts: bool = False


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _conflict_aware_insert(dialect_name: str, table: Any) -> Any:  # noqa: ANN401
    if dialect_name == "postgresql":
        return pg_insert(table)
    return sqlite_insert(table)


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
        next_attempt_at=_as_utc(row["next_attempt_at"]),
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
                    "next_attempt_at": draft.next_attempt_at or draft.created_at,
                }
                for draft in drafts
                if draft.id not in existing
            ]
            if rows:
                connection.execute(insert(invoice_drafts), rows)

    def record_smoke(self, draft: InvoiceDraft, provider_invoice_id: str, at: datetime) -> None:
        with self.engine.begin() as connection:
            upsert = _conflict_aware_insert(self.engine.dialect.name, trial_runs)
            connection.execute(
                upsert.values(
                    id=SMOKE_RUN_ID,
                    status=TrialStatus.COMPLETED,
                    started_at=at,
                    ends_at=at,
                    created_at=at,
                    active_marker=None,
                ).on_conflict_do_nothing(index_elements=[trial_runs.c.id])
            )
            upsert = _conflict_aware_insert(self.engine.dialect.name, invoice_batches)
            connection.execute(
                upsert.values(
                    id=SMOKE_BATCH_ID,
                    run_id=SMOKE_RUN_ID,
                    slot_index=0,
                    scheduled_at=at,
                    target_count=12,
                    status=BatchStatus.COMPLETED,
                    attempts=0,
                    created_at=at,
                ).on_conflict_do_nothing(index_elements=[invoice_batches.c.id])
            )
            upsert = _conflict_aware_insert(self.engine.dialect.name, invoice_drafts)
            connection.execute(
                upsert.values(
                    id=str(draft.id),
                    batch_id=SMOKE_BATCH_ID,
                    ordinal=draft.ordinal,
                    payer_name=draft.payer_name,
                    payer_tax_id=draft.payer_tax_id,
                    amount=int(draft.amount),
                    tag=draft.tag,
                    status=DraftStatus.CREATED,
                    provider_invoice_id=provider_invoice_id,
                    attempts=0,
                    reconcile_attempts=0,
                    created_at=at,
                    updated_at=at,
                    next_attempt_at=at,
                ).on_conflict_do_nothing(index_elements=[invoice_drafts.c.id])
            )

    def pending(self, batch_id: BatchId, now: datetime | None = None) -> tuple[InvoiceDraft, ...]:
        effective_now = now or datetime.now(UTC)
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(invoice_drafts)
                .where(
                    invoice_drafts.c.batch_id == batch_id,
                    invoice_drafts.c.status == DraftStatus.PENDING,
                    invoice_drafts.c.next_attempt_at <= effective_now,
                )
                .order_by(invoice_drafts.c.ordinal)
            ).mappings()
            return tuple(_draft(row) for row in rows)

    def unknown(self, now: datetime | None = None, limit: int = 100) -> tuple[InvoiceDraft, ...]:
        effective_now = now or datetime.now(UTC)
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(invoice_drafts)
                .where(
                    invoice_drafts.c.status == DraftStatus.UNKNOWN,
                    invoice_drafts.c.next_attempt_at <= effective_now,
                )
                .order_by(invoice_drafts.c.updated_at)
                .limit(limit)
            ).mappings()
            return tuple(_draft(row) for row in rows)

    def created(self, result: DraftCreated) -> None:
        self._transition(
            _DraftTransition(
                result.draft_id,
                DraftStatus.CREATED,
                result.at,
                result.invoice_id,
                None,
                result.at,
                reset_reconcile_attempts=True,
            )
        )

    def unknown_result(self, result: DraftFailure) -> None:
        self._transition(
            _DraftTransition(
                result.draft_id,
                DraftStatus.UNKNOWN,
                result.at,
                None,
                result.error_code,
                result.at,
            )
        )

    def retry(self, result: DraftFailure, delay: timedelta, max_attempts: int) -> bool:
        with self.engine.begin() as connection:
            attempts = (
                int(
                    connection.execute(
                        select(invoice_drafts.c.attempts).where(
                            invoice_drafts.c.id == result.draft_id
                        )
                    ).scalar_one()
                )
                + 1
            )
            exhausted = attempts >= max_attempts
            connection.execute(
                update(invoice_drafts)
                .where(invoice_drafts.c.id == result.draft_id)
                .values(
                    status=DraftStatus.FAILED if exhausted else DraftStatus.PENDING,
                    provider_invoice_id=None,
                    attempts=attempts,
                    last_error_code="retry_exhausted" if exhausted else result.error_code,
                    next_attempt_at=result.at if exhausted else result.at + delay,
                    updated_at=result.at,
                )
            )
        return not exhausted

    def failed(self, result: DraftFailure) -> None:
        self._transition(
            _DraftTransition(
                result.draft_id,
                DraftStatus.FAILED,
                result.at,
                None,
                result.error_code,
                result.at,
            )
        )

    def reconcile(
        self,
        result: InvoiceReconciliation,
        *,
        max_attempts: int,
        max_reconciliation_attempts: int = MAX_RECONCILIATION_ATTEMPTS,
        retry_delay: timedelta = timedelta(seconds=5),
    ) -> None:
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
                if next_attempts >= max_reconciliation_attempts
                else DraftStatus.UNKNOWN
            )
            exhausted = (
                next_status is DraftStatus.PENDING and int(result.draft.attempts) >= max_attempts
            )
            connection.execute(
                update(invoice_drafts)
                .where(invoice_drafts.c.id == result.draft.id)
                .values(
                    status=DraftStatus.FAILED if exhausted else next_status,
                    reconcile_attempts=0 if next_status is DraftStatus.PENDING else next_attempts,
                    last_error_code="retry_exhausted" if exhausted else "reconciliation_not_found",
                    next_attempt_at=result.at if exhausted else result.at + retry_delay,
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
        transition: _DraftTransition,
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(invoice_drafts)
                .where(invoice_drafts.c.id == transition.draft_id)
                .values(
                    status=transition.status,
                    provider_invoice_id=transition.provider_invoice_id,
                    attempts=invoice_drafts.c.attempts + 1,
                    last_error_code=transition.error_code,
                    next_attempt_at=transition.next_attempt_at,
                    **({"reconcile_attempts": 0} if transition.reset_reconcile_attempts else {}),
                    updated_at=transition.at,
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
