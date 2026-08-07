from dataclasses import dataclass
from datetime import datetime

from starkbank_trial.domain.models import ScheduleSlot
from starkbank_trial.domain.status import BatchStatus, TrialStatus
from starkbank_trial.domain.types import BatchId, TrialRunId


@dataclass(frozen=True, slots=True)
class NewTrial:
    start_at: datetime
    schedule: tuple[ScheduleSlot, ...]


@dataclass(frozen=True, slots=True)
class TrialRun:
    id: TrialRunId
    status: TrialStatus
    started_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class StatusCount:
    status: str
    count: int


@dataclass(frozen=True, slots=True)
class TrialSummary:
    id: TrialRunId
    status: TrialStatus
    started_at: datetime
    ends_at: datetime
    next_batch_at: datetime | None


@dataclass(frozen=True, slots=True)
class TrialReport:
    trial: TrialSummary | None
    batches: tuple[StatusCount, ...]
    invoices: tuple[StatusCount, ...]
    webhook_events: tuple[StatusCount, ...]
    transfers: tuple[StatusCount, ...]


@dataclass(frozen=True, slots=True)
class BatchClaim:
    now: datetime
    lease_until: datetime
    max_attempts: int = 15


@dataclass(frozen=True, slots=True)
class InvoiceBatch:
    id: BatchId
    run_id: TrialRunId
    slot_index: int
    scheduled_at: datetime
    target_count: int
    status: BatchStatus
    lease_until: datetime
    attempts: int
