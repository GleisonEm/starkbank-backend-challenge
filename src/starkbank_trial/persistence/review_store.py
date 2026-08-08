import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, cast

from sqlalchemy import Engine, and_, false, func, or_, select
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.sql import ColumnElement, Select

from starkbank_trial.domain.constants import SMOKE_RUN_ID
from starkbank_trial.persistence.schema import (
    invoice_batches,
    invoice_drafts,
    transfer_jobs,
    trial_runs,
    webhook_events,
)

type JsonScalar = str | int | bool | None
type JsonValue = JsonScalar | list[JsonValue] | Mapping[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type ReviewRow = RowMapping
DEFAULT_LIMIT: Final = 25
MAX_LIMIT: Final = 100


@dataclass(frozen=True, slots=True)
class ReviewQuery:
    limit: int = DEFAULT_LIMIT
    cursor: str | None = None
    trial_id: str | None = None
    statuses: tuple[str, ...] = ()
    from_at: datetime | None = None
    to_at: datetime | None = None
    batch_id: str | None = None
    slot_index: int | None = None
    credit_status: str | None = None
    provider_invoice_id: str | None = None
    tag: str | None = None
    dispatch_status: str | None = None
    provider_status: str | None = None
    provider_log_type: str | None = None
    invoice_id: str | None = None
    provider_transfer_id: str | None = None
    external_id: str | None = None
    subscription: str | None = None
    log_type: str | None = None
    outcome: str | None = None
    resource_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewPage:
    items: tuple[JsonObject, ...]
    next_cursor: str | None
    total: int


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    effective = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return effective.isoformat()


def _cursor(value: datetime, identifier: str) -> str:
    raw = f"{_iso(value)}|{identifier}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str | None) -> tuple[datetime, str] | None:
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode()
        timestamp, identifier = decoded.split("|", 1)
        parsed = datetime.fromisoformat(timestamp)
    except (ValueError, UnicodeDecodeError):
        message = "invalid cursor"
        raise ValueError(message) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed, identifier


def _page(
    rows: list[JsonObject],
    total: int,
    limit: int,
    timestamps: list[tuple[datetime, str]],
) -> ReviewPage:
    has_more = len(rows) > limit
    visible = rows[:limit]
    next_cursor = _cursor(*timestamps[limit - 1]) if has_more else None
    return ReviewPage(tuple(visible), next_cursor, total)


@dataclass(frozen=True, slots=True)
class ReviewStore:
    engine: Engine

    def overview(self, trial_id: str | None = None) -> JsonObject:
        with self.engine.connect() as connection:
            trial = self._trial_row(connection, trial_id)
            trial_key = str(trial["id"]) if trial is not None else None
            report = self._counts(connection, trial_key)
            totals_statement = (
                select(
                    func.coalesce(func.sum(transfer_jobs.c.amount), 0),
                    func.coalesce(func.sum(transfer_jobs.c.fee), 0),
                    func.coalesce(func.sum(transfer_jobs.c.net_amount), 0),
                )
                .select_from(
                    transfer_jobs.join(
                        invoice_drafts,
                        invoice_drafts.c.provider_invoice_id == transfer_jobs.c.invoice_id,
                    ).join(invoice_batches, invoice_batches.c.id == invoice_drafts.c.batch_id)
                )
                .where(transfer_jobs.c.status.in_(("succeeded", "permanent_failure")))
            )
            if trial_key is None:
                totals_statement = totals_statement.where(false())
            else:
                totals_statement = totals_statement.where(invoice_batches.c.run_id == trial_key)
            totals = connection.execute(totals_statement).one()
        return {
            "generated_at": _iso(datetime.now(UTC)),
            "trial": self._trial_payload(trial) if trial is not None else None,
            "schedule": {
                "interval_hours": 3,
                "duration_hours": 24,
                "slot_count": 8,
                "batch_size": {"min": 8, "max": 12},
            },
            "counts": report,
            "transfer_totals_cents": {
                "amount": int(totals[0]),
                "fee": int(totals[1]),
                "net_amount": int(totals[2]),
            },
        }

    def trials(self, query: ReviewQuery) -> ReviewPage:
        statement = select(trial_runs).where(trial_runs.c.id != SMOKE_RUN_ID)
        count = select(func.count()).select_from(trial_runs).where(trial_runs.c.id != SMOKE_RUN_ID)
        conditions: list[Any] = []
        if query.trial_id is not None:
            conditions.append(trial_runs.c.id == query.trial_id)
        if query.statuses:
            conditions.append(trial_runs.c.status.in_(query.statuses))
        conditions.extend(_time_conditions(trial_runs.c.created_at, query))
        if conditions:
            expression = and_(*conditions)
            statement = statement.where(expression)
            count = count.where(expression)
        return self._mapping_page(
            statement.order_by(trial_runs.c.created_at.desc(), trial_runs.c.id.desc()),
            count,
            trial_runs.c.created_at,
            trial_runs.c.id,
            query,
            self._trial_payload,
        )

    def trial(self, trial_id: str) -> JsonObject | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(trial_runs).where(
                        trial_runs.c.id == trial_id, trial_runs.c.id != SMOKE_RUN_ID
                    )
                )
                .mappings()
                .first()
            )
        return self._trial_payload(row) if row is not None else None

    def batches(self, query: ReviewQuery) -> ReviewPage:
        statement = select(invoice_batches, trial_runs.c.status.label("trial_status")).join(
            trial_runs, trial_runs.c.id == invoice_batches.c.run_id
        )
        count = (
            select(func.count())
            .select_from(invoice_batches)
            .where(invoice_batches.c.run_id != SMOKE_RUN_ID)
        )
        statement = statement.where(invoice_batches.c.run_id != SMOKE_RUN_ID)
        if query.trial_id is not None:
            statement = statement.where(invoice_batches.c.run_id == query.trial_id)
            count = count.where(invoice_batches.c.run_id == query.trial_id)
        if query.statuses:
            statement = statement.where(invoice_batches.c.status.in_(query.statuses))
            count = count.where(invoice_batches.c.status.in_(query.statuses))
        if query.slot_index is not None:
            statement = statement.where(invoice_batches.c.slot_index == query.slot_index)
            count = count.where(invoice_batches.c.slot_index == query.slot_index)
        time_conditions = _time_conditions(invoice_batches.c.scheduled_at, query)
        if time_conditions:
            expression = and_(*time_conditions)
            statement = statement.where(expression)
            count = count.where(expression)
        statement = statement.order_by(
            invoice_batches.c.scheduled_at.desc(), invoice_batches.c.id.desc()
        )
        return self._mapping_page(
            statement,
            count,
            invoice_batches.c.scheduled_at,
            invoice_batches.c.id,
            query,
            self._batch_payload,
        )

    def batch(self, batch_id: str) -> JsonObject | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(invoice_batches, trial_runs.c.status.label("trial_status"))
                    .join(trial_runs, trial_runs.c.id == invoice_batches.c.run_id)
                    .where(
                        invoice_batches.c.id == batch_id,
                        invoice_batches.c.run_id != SMOKE_RUN_ID,
                    )
                )
                .mappings()
                .first()
            )
        return self._batch_payload(row) if row is not None else None

    def invoices(self, query: ReviewQuery) -> ReviewPage:
        credited_at = (
            select(func.max(webhook_events.c.received_at))
            .where(webhook_events.c.invoice_id == invoice_drafts.c.provider_invoice_id)
            .scalar_subquery()
        )
        statement = select(
            invoice_drafts,
            invoice_batches.c.run_id,
            invoice_batches.c.status.label("batch_status"),
            credited_at.label("credited_at"),
        ).join(invoice_batches, invoice_batches.c.id == invoice_drafts.c.batch_id)
        count = (
            select(func.count())
            .select_from(invoice_drafts)
            .join(invoice_batches, invoice_batches.c.id == invoice_drafts.c.batch_id)
            .where(invoice_batches.c.run_id != SMOKE_RUN_ID)
        )
        statement = statement.where(invoice_batches.c.run_id != SMOKE_RUN_ID)
        statement, count = self._invoice_filters(statement, count, query)
        statement = statement.order_by(
            invoice_drafts.c.created_at.desc(), invoice_drafts.c.id.desc()
        )
        return self._mapping_page(
            statement,
            count,
            invoice_drafts.c.created_at,
            invoice_drafts.c.id,
            query,
            self._invoice_payload,
        )

    def invoice(self, draft_id: str) -> JsonObject | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(
                        invoice_drafts,
                        invoice_batches.c.run_id,
                        invoice_batches.c.status.label("batch_status"),
                        select(func.max(webhook_events.c.received_at))
                        .where(webhook_events.c.invoice_id == invoice_drafts.c.provider_invoice_id)
                        .scalar_subquery()
                        .label("credited_at"),
                    )
                    .join(invoice_batches, invoice_batches.c.id == invoice_drafts.c.batch_id)
                    .where(
                        invoice_drafts.c.id == draft_id,
                        invoice_batches.c.run_id != SMOKE_RUN_ID,
                    )
                )
                .mappings()
                .first()
            )
        return self._invoice_payload(row) if row is not None else None

    def transfers(self, query: ReviewQuery) -> ReviewPage:
        statement = (
            select(
                transfer_jobs,
                invoice_drafts.c.batch_id,
                invoice_batches.c.run_id,
            )
            .outerjoin(
                invoice_drafts, invoice_drafts.c.provider_invoice_id == transfer_jobs.c.invoice_id
            )
            .outerjoin(invoice_batches, invoice_batches.c.id == invoice_drafts.c.batch_id)
        )
        count = select(func.count()).select_from(transfer_jobs)
        statement, count = self._transfer_filters(statement, count, query)
        statement = statement.order_by(transfer_jobs.c.created_at.desc(), transfer_jobs.c.id.desc())
        return self._mapping_page(
            statement,
            count,
            transfer_jobs.c.created_at,
            transfer_jobs.c.id,
            query,
            self._transfer_payload,
        )

    def transfer(self, job_id: str) -> JsonObject | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(transfer_jobs, invoice_drafts.c.batch_id, invoice_batches.c.run_id)
                    .outerjoin(
                        invoice_drafts,
                        invoice_drafts.c.provider_invoice_id == transfer_jobs.c.invoice_id,
                    )
                    .outerjoin(invoice_batches, invoice_batches.c.id == invoice_drafts.c.batch_id)
                    .where(transfer_jobs.c.id == job_id)
                )
                .mappings()
                .first()
            )
        return self._transfer_payload(row) if row is not None else None

    def events(self, query: ReviewQuery) -> ReviewPage:
        statement = select(webhook_events)
        count = select(func.count()).select_from(webhook_events)
        filters: list[ColumnElement[bool]] = []
        if query.subscription:
            filters.append(webhook_events.c.subscription == query.subscription)
        if query.log_type:
            filters.append(webhook_events.c.log_type == query.log_type)
        if query.outcome:
            filters.append(webhook_events.c.outcome == query.outcome)
        if query.resource_id:
            filters.append(
                or_(
                    webhook_events.c.invoice_id == query.resource_id,
                    webhook_events.c.transfer_id == query.resource_id,
                )
            )
        if query.trial_id is not None:
            trial_invoice_ids = (
                select(invoice_drafts.c.provider_invoice_id)
                .join(invoice_batches, invoice_batches.c.id == invoice_drafts.c.batch_id)
                .where(
                    invoice_batches.c.run_id == query.trial_id,
                    invoice_drafts.c.provider_invoice_id.is_not(None),
                )
            )
            trial_transfer_ids = (
                select(transfer_jobs.c.provider_transfer_id)
                .join(
                    invoice_drafts,
                    invoice_drafts.c.provider_invoice_id == transfer_jobs.c.invoice_id,
                )
                .join(invoice_batches, invoice_batches.c.id == invoice_drafts.c.batch_id)
                .where(
                    invoice_batches.c.run_id == query.trial_id,
                    transfer_jobs.c.provider_transfer_id.is_not(None),
                )
            )
            filters.append(
                or_(
                    webhook_events.c.invoice_id.in_(trial_invoice_ids),
                    webhook_events.c.transfer_id.in_(trial_transfer_ids),
                )
            )
        if query.from_at:
            filters.append(webhook_events.c.received_at >= query.from_at)
        if query.to_at:
            filters.append(webhook_events.c.received_at <= query.to_at)
        if filters:
            statement = statement.where(and_(*filters))
            count = count.where(and_(*filters))
        statement = statement.order_by(
            webhook_events.c.received_at.desc(), webhook_events.c.id.desc()
        )
        return self._mapping_page(
            statement,
            count,
            webhook_events.c.received_at,
            webhook_events.c.id,
            query,
            self._event_payload,
        )

    def event(self, event_id: str) -> JsonObject | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(webhook_events).where(webhook_events.c.id == event_id))
                .mappings()
                .first()
            )
        return self._event_payload(row) if row is not None else None

    def _mapping_page(  # noqa: PLR0913, PLR0917
        self,
        statement: Select[Any],
        count: Select[Any],
        timestamp: ColumnElement[Any],
        identifier: ColumnElement[Any],
        query: ReviewQuery,
        mapper: Callable[[ReviewRow], JsonObject],
    ) -> ReviewPage:
        decoded = _decode_cursor(query.cursor)
        if decoded is not None:
            cursor_time, cursor_id = decoded
            statement = statement.where(
                or_(timestamp < cursor_time, and_(timestamp == cursor_time, identifier < cursor_id))
            )
        statement = statement.limit(query.limit + 1)
        with self.engine.connect() as connection:
            rows = list(connection.execute(statement).mappings())
            total = int(connection.execute(count).scalar_one())
        mapped = [mapper(row) for row in rows]
        timestamps = [
            (normalize_row_time(row, timestamp.name), str(row[identifier.name])) for row in rows
        ]
        return _page(mapped, total, query.limit, timestamps)

    @staticmethod
    def _trial_row(connection: Connection, trial_id: str | None) -> ReviewRow | None:
        statement = select(trial_runs).where(trial_runs.c.id != SMOKE_RUN_ID)
        if trial_id is not None:
            statement = statement.where(trial_runs.c.id == trial_id)
        return (
            connection.execute(statement.order_by(trial_runs.c.created_at.desc()).limit(1))
            .mappings()
            .first()
        )

    @staticmethod
    def _counts(connection: Connection, trial_id: str | None) -> JsonObject:
        if trial_id is None:
            return {"batches": {}, "invoices": {}, "webhook_events": {}, "transfers": {}}

        def grouped(
            table: Any,  # noqa: ANN401
            column: ColumnElement[Any],
            where: Any = None,  # noqa: ANN401
        ) -> dict[str, int]:
            statement = (
                select(column, func.count()).select_from(table).group_by(column).order_by(column)
            )
            if where is not None:
                statement = statement.where(where)
            return {str(row[0]): int(row[1]) for row in connection.execute(statement)}

        batch_where = invoice_batches.c.run_id == trial_id
        invoice_where = invoice_batches.c.run_id == trial_id
        invoice_from = invoice_drafts.join(
            invoice_batches, invoice_drafts.c.batch_id == invoice_batches.c.id
        )
        trial_invoice_ids = (
            select(invoice_drafts.c.provider_invoice_id)
            .select_from(
                invoice_drafts.join(
                    invoice_batches, invoice_drafts.c.batch_id == invoice_batches.c.id
                )
            )
            .where(
                invoice_batches.c.run_id == trial_id,
                invoice_drafts.c.provider_invoice_id.is_not(None),
            )
        )
        trial_transfer_ids = (
            select(transfer_jobs.c.provider_transfer_id)
            .select_from(
                transfer_jobs.join(
                    invoice_drafts,
                    invoice_drafts.c.provider_invoice_id == transfer_jobs.c.invoice_id,
                ).join(invoice_batches, invoice_batches.c.id == invoice_drafts.c.batch_id)
            )
            .where(
                invoice_batches.c.run_id == trial_id,
                transfer_jobs.c.provider_transfer_id.is_not(None),
            )
        )
        event_where = or_(
            webhook_events.c.invoice_id.in_(trial_invoice_ids),
            webhook_events.c.transfer_id.in_(trial_transfer_ids),
        )
        transfer_from = transfer_jobs.join(
            invoice_drafts, invoice_drafts.c.provider_invoice_id == transfer_jobs.c.invoice_id
        ).join(invoice_batches, invoice_batches.c.id == invoice_drafts.c.batch_id)
        return {
            "batches": grouped(invoice_batches, invoice_batches.c.status, batch_where),
            "invoices": grouped(invoice_from, invoice_drafts.c.status, invoice_where),
            "webhook_events": grouped(webhook_events, webhook_events.c.outcome, event_where),
            "transfers": grouped(transfer_from, transfer_jobs.c.status, batch_where),
        }

    @staticmethod
    def _trial_payload(row: ReviewRow) -> JsonObject:
        return {
            "id": str(row["id"]),
            "status": str(row["status"]),
            "started_at": _iso(row["started_at"]),
            "ends_at": _iso(row["ends_at"]),
            "created_at": _iso(row["created_at"]),
            "completed_at": _iso(row["completed_at"]),
        }

    @staticmethod
    def _batch_payload(row: ReviewRow) -> JsonObject:
        return {
            "id": str(row["id"]),
            "trial_id": str(row["run_id"]),
            "slot_index": int(row["slot_index"]),
            "scheduled_at": _iso(row["scheduled_at"]),
            "target_count": int(row["target_count"]),
            "status": str(row["status"]),
            "trial_status": str(row["trial_status"]),
            "attempts": int(row["attempts"]),
            "lease_until": _iso(row["lease_until"]),
            "completed_at": _iso(row["completed_at"]),
        }

    @staticmethod
    def _invoice_payload(row: ReviewRow) -> JsonObject:
        return {
            "id": str(row["id"]),
            "trial_id": str(row["run_id"]),
            "batch_id": str(row["batch_id"]),
            "ordinal": int(row["ordinal"]),
            "amount_cents": int(row["amount"]),
            "tag": str(row["tag"]),
            "status": str(row["status"]),
            "credit_status": "credited" if row["credited_at"] is not None else "not_observed",
            "provider_invoice_id": row["provider_invoice_id"],
            "attempts": int(row["attempts"]),
            "reconcile_attempts": int(row["reconcile_attempts"]),
            "last_error_code": row["last_error_code"],
            "credited_at": _iso(row["credited_at"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "next_attempt_at": _iso(row["next_attempt_at"]),
        }

    @staticmethod
    def _transfer_payload(row: ReviewRow) -> JsonObject:
        return {
            "id": str(row["id"]),
            "trial_id": str(row["run_id"]) if row.get("run_id") is not None else None,
            "batch_id": str(row["batch_id"]) if row.get("batch_id") is not None else None,
            "event_id": str(row["event_id"]),
            "invoice_id": str(row["invoice_id"]),
            "provider_transfer_id": row["provider_transfer_id"],
            "amount_cents": int(row["amount"]),
            "fee_cents": int(row["fee"]),
            "net_amount_cents": int(row["net_amount"]),
            "external_id": str(row["external_id"]),
            "tag": str(row["tag"]),
            "dispatch_status": str(row["status"]),
            "provider_status": row["provider_status"],
            "provider_log_type": row["provider_log_type"],
            "provider_status_updated_at": _iso(row["provider_status_updated_at"]),
            "attempts": int(row["attempts"]),
            "next_attempt_at": _iso(row["next_attempt_at"]),
            "last_error_code": row["last_error_code"],
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
        }

    @staticmethod
    def _event_payload(row: ReviewRow) -> JsonObject:
        return {
            "id": str(row["id"]),
            "subscription": str(row["subscription"]),
            "log_type": str(row["log_type"]),
            "invoice_id": row["invoice_id"],
            "transfer_id": row["transfer_id"],
            "outcome": str(row["outcome"]),
            "received_at": _iso(row["received_at"]),
        }

    @staticmethod
    def _invoice_filters(
        statement: Select[Any], count: Select[Any], query: ReviewQuery
    ) -> tuple[Select[Any], Select[Any]]:
        conditions: list[Any] = []
        for column, value in (
            (invoice_batches.c.run_id, query.trial_id),
            (invoice_drafts.c.batch_id, query.batch_id),
            (invoice_drafts.c.provider_invoice_id, query.provider_invoice_id),
            (invoice_drafts.c.tag, query.tag),
        ):
            if value is not None:
                conditions.append(column == value)
        if query.statuses:
            conditions.append(invoice_drafts.c.status.in_(query.statuses))
        if query.credit_status == "credited":
            conditions.append(
                invoice_drafts.c.provider_invoice_id.in_(
                    select(webhook_events.c.invoice_id).where(
                        webhook_events.c.invoice_id.is_not(None)
                    )
                )
            )
        elif query.credit_status == "not_observed":
            conditions.append(
                ~invoice_drafts.c.provider_invoice_id.in_(
                    select(webhook_events.c.invoice_id).where(
                        webhook_events.c.invoice_id.is_not(None)
                    )
                )
            )
        conditions.extend(_time_conditions(invoice_drafts.c.created_at, query))
        if conditions:
            expression = and_(*conditions)
            statement = statement.where(expression)
            count = count.where(expression)
        return statement, count

    @staticmethod
    def _transfer_filters(
        statement: Select[Any], count: Select[Any], query: ReviewQuery
    ) -> tuple[Select[Any], Select[Any]]:
        conditions: list[Any] = []
        for column, value in (
            (invoice_batches.c.run_id, query.trial_id),
            (invoice_drafts.c.batch_id, query.batch_id),
            (transfer_jobs.c.invoice_id, query.invoice_id),
            (transfer_jobs.c.provider_transfer_id, query.provider_transfer_id),
            (transfer_jobs.c.external_id, query.external_id),
        ):
            if value is not None:
                conditions.append(column == value)
        for column, value in (
            (transfer_jobs.c.status, query.dispatch_status),
            (transfer_jobs.c.provider_status, query.provider_status),
            (transfer_jobs.c.provider_log_type, query.provider_log_type),
        ):
            if value is not None:
                conditions.append(column == value)
        conditions.extend(_time_conditions(transfer_jobs.c.created_at, query))
        if conditions:
            expression = and_(*conditions)
            statement = statement.where(expression)
            count = count.select_from(
                transfer_jobs.outerjoin(
                    invoice_drafts,
                    invoice_drafts.c.provider_invoice_id == transfer_jobs.c.invoice_id,
                ).outerjoin(invoice_batches, invoice_batches.c.id == invoice_drafts.c.batch_id)
            ).where(expression)
        return statement, count


def _time_conditions(column: ColumnElement[Any], query: ReviewQuery) -> list[Any]:
    conditions: list[Any] = []
    if query.from_at:
        conditions.append(column >= query.from_at)
    if query.to_at:
        conditions.append(column <= query.to_at)
    return conditions


def normalize_row_time(row: ReviewRow, name: str) -> datetime:
    value = cast("datetime", row[name])
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
