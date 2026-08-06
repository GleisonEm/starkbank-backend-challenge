import json
from pathlib import Path

import structlog

from starkbank_trial.logging import configure_logging


def test_configure_logging_writes_structured_events_to_file(tmp_path: Path) -> None:
    # Given
    log_file = tmp_path / "logs" / "trial.jsonl"
    configure_logging("info", log_file)

    # When
    structlog.get_logger("test").info("webhook_recorded", outcome="ignored")

    # Then
    payload = json.loads(log_file.read_text(encoding="utf-8"))
    assert payload["event"] == "webhook_recorded"
    assert payload["level"] == "info"
    assert payload["outcome"] == "ignored"
    assert "timestamp" in payload
