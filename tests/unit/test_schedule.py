from datetime import UTC, datetime, timedelta

import pytest

from starkbank_trial.application.schedule import build_schedule
from starkbank_trial.domain.errors import InvalidBatchCountError


def test_build_schedule_creates_eight_slots_over_twenty_four_hours() -> None:
    # Given
    start = datetime(2026, 8, 1, 12, tzinfo=UTC)
    counts = (8, 9, 10, 11, 12, 8, 9, 10)

    # When
    slots = build_schedule(start, counts)

    # Then
    assert tuple(slot.scheduled_at for slot in slots) == tuple(
        start + timedelta(hours=offset) for offset in range(0, 24, 3)
    )
    assert tuple(slot.target_count for slot in slots) == counts


@pytest.mark.parametrize(
    "counts",
    [
        (8,) * 7,
        (8,) * 9,
        (7,) + (8,) * 7,
        (13,) + (8,) * 7,
    ],
)
def test_build_schedule_rejects_invalid_slot_counts(counts: tuple[int, ...]) -> None:
    # Given / When / Then
    with pytest.raises(InvalidBatchCountError):
        build_schedule(datetime(2026, 8, 1, tzinfo=UTC), counts)
