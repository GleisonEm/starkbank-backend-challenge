from enum import StrEnum, unique


@unique
class TrialStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    STOPPED = "stopped"
    DEGRADED = "degraded"


@unique
class BatchStatus(StrEnum):
    SCHEDULED = "scheduled"
    ISSUING = "issuing"
    RECONCILING = "reconciling"
    COMPLETED = "completed"
    DEGRADED = "degraded"


@unique
class DraftStatus(StrEnum):
    PENDING = "pending"
    CREATED = "created"
    UNKNOWN = "unknown"
    FAILED = "failed"


@unique
class TransferStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    UNKNOWN = "unknown"
    SUCCEEDED = "succeeded"
    PERMANENT_FAILURE = "permanent_failure"
