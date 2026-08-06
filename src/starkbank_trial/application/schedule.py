from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final

from starkbank_trial.domain.errors import InvalidBatchCountError, InvalidStartTimeError
from starkbank_trial.domain.models import ScheduleSlot

SLOT_COUNT: Final = 8
MIN_BATCH_SIZE: Final = 8
MAX_BATCH_SIZE: Final = 12


def build_schedule(start_at: datetime, counts: Sequence[int]) -> tuple[ScheduleSlot, ...]:
    if start_at.tzinfo is None or start_at.utcoffset() is None:
        raise InvalidStartTimeError
    normalized_counts = tuple(counts)
    if len(normalized_counts) != SLOT_COUNT or any(
        count < MIN_BATCH_SIZE or count > MAX_BATCH_SIZE for count in normalized_counts
    ):
        raise InvalidBatchCountError(counts=normalized_counts)
    return tuple(
        ScheduleSlot(
            index=index,
            scheduled_at=start_at + timedelta(hours=index * 3),
            target_count=target_count,
        )
        for index, target_count in enumerate(normalized_counts)
    )
