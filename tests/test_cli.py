import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect, text
from typer.testing import CliRunner

import starkbank_trial.cli as cli_module
from starkbank_trial.application.clock import SystemClock
from starkbank_trial.application.transfers import TransferWorker, WorkerResult, should_poll
from starkbank_trial.application.trials import TrialService
from starkbank_trial.application.webhooks import WebhookService
from starkbank_trial.bootstrap import Services
from starkbank_trial.config import Settings
from starkbank_trial.domain.events import IgnoredEvent, VerifiedEvent
from starkbank_trial.domain.invoices import InvoiceDraft, ProviderInvoice
from starkbank_trial.domain.models import TransferCommand
from starkbank_trial.domain.provider import (
    ProviderPermanentError,
    ProviderTransfer,
    ProviderUnknownOutcomeError,
    ProviderWebhook,
    WebhookInspection,
)
from starkbank_trial.domain.types import EventId, InvoiceId, TransferId
from starkbank_trial.persistence.invoice_store import InvoiceStore
from starkbank_trial.persistence.schema import metadata
from starkbank_trial.persistence.stores import build_stores


@dataclass(frozen=True, slots=True)
class FixedCounts:
    def next(self) -> tuple[int, ...]:
        return (8, 8, 8, 8, 8, 8, 8, 8)


@dataclass(slots=True)
class FakeGateway:
    created_drafts: list[InvoiceDraft] = field(default_factory=list)

    def create_invoice(self, draft: InvoiceDraft) -> ProviderInvoice:
        self.created_drafts.append(draft)
        return ProviderInvoice(InvoiceId(f"invoice-{draft.id}"), draft.tag)

    def find_invoice(self, tag: str) -> ProviderInvoice | None:
        return None

    def ensure_transfer(self, command: TransferCommand) -> ProviderTransfer:
        return ProviderTransfer(TransferId("transfer-1"), command.external_id, "created")

    def find_transfer(self, command: TransferCommand) -> ProviderTransfer | None:
        return None

    def verify_event(self, content: bytes, signature: str) -> VerifiedEvent:
        return IgnoredEvent(EventId("event-1"), "transfer", "created", "workspace-1")

    def list_webhooks(self) -> tuple[ProviderWebhook, ...]:
        return (
            ProviderWebhook(
                id="webhook-1",
                url="https://trial.example.com/webhooks/starkbank",
                subscriptions=("invoice",),
            ),
        )

    def inspect_webhooks(self, url: str) -> WebhookInspection:
        matching = [webhook for webhook in self.list_webhooks() if webhook.url == url]
        active = next(
            (
                webhook
                for webhook in matching
                if set(webhook.subscriptions) == {"invoice", "transfer"}
            ),
            None,
        )
        stale = tuple(webhook for webhook in matching if webhook is not active)
        return WebhookInspection(active=active, stale=stale)


@pytest.fixture
def services(tmp_path: Path) -> Iterator[Services]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'cli.db'}")
    metadata.create_all(engine)
    stores = build_stores(engine)
    gateway = FakeGateway()
    clock = SystemClock()
    yield Services(
        engine,
        stores,
        TrialService(stores, gateway, clock, FixedCounts()),
        WebhookService(gateway, stores.events, clock),
        TransferWorker(stores.transfers, gateway, clock),
    )
    engine.dispose()


def test_trial_and_worker_commands_emit_machine_readable_results(
    monkeypatch: pytest.MonkeyPatch,
    services: Services,
) -> None:
    # Given
    runner = CliRunner()

    def fixed_services() -> Services:
        return services

    monkeypatch.setattr(cli_module, "_services", fixed_services)

    # When
    started = runner.invoke(
        cli_module.app,
        ["trial", "start", "--start-at", datetime.now(UTC).isoformat()],
    )
    ticked = runner.invoke(cli_module.app, ["trial", "tick"])
    reconciled = runner.invoke(cli_module.app, ["trial", "reconcile"])
    worker = runner.invoke(cli_module.app, ["worker", "once"])
    status = runner.invoke(cli_module.app, ["trial", "status"])

    # Then
    assert started.exit_code == 0
    assert json.loads(started.stdout)["status"] == "active"
    assert json.loads(ticked.stdout) == {"result": "completed"}
    assert json.loads(reconciled.stdout) == {"reconciled": 0}
    assert json.loads(worker.stdout) == {"result": "idle"}
    report = json.loads(status.stdout)
    assert report["trial"]["status"] == "active"
    assert report["batches"] == {"completed": 1, "scheduled": 7}
    assert report["invoices"] == {"created": 8}
    assert report["webhook_events"] == {}
    assert report["transfers"] == {}


def test_provider_list_webhooks_emits_safe_machine_readable_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    gateway = FakeGateway()

    def fixed_client(settings: Settings) -> FakeGateway:
        return gateway

    monkeypatch.setattr(cli_module, "build_client", fixed_client)

    # When
    result = CliRunner().invoke(cli_module.app, ["provider", "list-webhooks"])

    # Then
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "webhooks": [
            {
                "id": "webhook-1",
                "subscriptions": ["invoice"],
                "url": "https://trial.example.com/webhooks/starkbank",
            }
        ]
    }


def test_provider_cleanup_webhooks_replaces_incomplete_webhook_for_target_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CleanupGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.deleted: list[str] = []

        def list_webhooks(self) -> tuple[ProviderWebhook, ...]:
            return (
                ProviderWebhook(
                    id="webhook-stale",
                    url="https://trial.example.com/webhooks/starkbank",
                    subscriptions=("invoice",),
                ),
                ProviderWebhook(
                    id="webhook-other-url",
                    url="https://old.example.com/webhooks/starkbank",
                    subscriptions=("invoice", "transfer"),
                ),
            )

        def delete_webhook(self, webhook_id: str) -> None:
            self.deleted.append(webhook_id)

        def ensure_webhook(self, url: str) -> ProviderWebhook:
            for stale in self.list_webhooks():
                if stale.url == url:
                    self.delete_webhook(stale.id)
            return ProviderWebhook("webhook-active", url, ("invoice", "transfer"))

    gateway = CleanupGateway()
    monkeypatch.setenv("STARKBANK_SANDBOX_LIVE_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://trial.example.com")

    def fixed_client(settings: Settings) -> CleanupGateway:
        return gateway

    monkeypatch.setattr(cli_module, "build_client", fixed_client)

    result = CliRunner().invoke(
        cli_module.app,
        ["provider", "cleanup-webhooks", "--confirm-sandbox"],
    )

    assert result.exit_code == 0
    assert gateway.deleted == ["webhook-stale"]
    assert json.loads(result.stdout) == {
        "active_url": "https://trial.example.com/webhooks/starkbank",
        "active_id": "webhook-active",
        "replaced_webhook_ids": ["webhook-stale"],
        "subscriptions": ["invoice", "transfer"],
    }


def test_provider_cleanup_webhooks_is_noop_when_active_webhook_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CleanupGateway(FakeGateway):
        def list_webhooks(self) -> tuple[ProviderWebhook, ...]:
            return (
                ProviderWebhook(
                    id="webhook-active",
                    url="https://trial.example.com/webhooks/starkbank",
                    subscriptions=("invoice", "transfer"),
                ),
            )

    gateway = CleanupGateway()
    monkeypatch.setenv("STARKBANK_SANDBOX_LIVE_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://trial.example.com")

    def fixed_client(settings: Settings) -> CleanupGateway:
        return gateway

    monkeypatch.setattr(cli_module, "build_client", fixed_client)

    result = CliRunner().invoke(
        cli_module.app,
        ["provider", "cleanup-webhooks", "--confirm-sandbox"],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "active_url": "https://trial.example.com/webhooks/starkbank",
        "active_id": "webhook-active",
        "replaced_webhook_ids": [],
        "subscriptions": ["invoice", "transfer"],
    }


def test_provider_setup_webhook_reports_replaced_incomplete_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SetupGateway(FakeGateway):
        def list_webhooks(self) -> tuple[ProviderWebhook, ...]:
            return (
                ProviderWebhook(
                    id="webhook-stale",
                    url="https://trial.example.com/webhooks/starkbank",
                    subscriptions=("invoice",),
                ),
            )

        def ensure_webhook(self, url: str) -> ProviderWebhook:
            return ProviderWebhook("webhook-active", url, ("invoice", "transfer"))

    gateway = SetupGateway()
    monkeypatch.setenv("STARKBANK_SANDBOX_LIVE_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://trial.example.com")

    def fixed_client(settings: Settings) -> SetupGateway:
        return gateway

    monkeypatch.setattr(cli_module, "build_client", fixed_client)

    result = CliRunner().invoke(cli_module.app, ["provider", "setup-webhook"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "webhook_id": "webhook-active",
        "url": "https://trial.example.com/webhooks/starkbank",
        "subscriptions": ["invoice", "transfer"],
        "replaced_webhook_ids": ["webhook-stale"],
        "notice": (
            "the previous webhook for this URL was incomplete and was replaced; "
            "delivery resumes with the complete webhook"
        ),
    }


def test_provider_failure_emits_sanitized_machine_readable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    class FailingGateway(FakeGateway):
        def list_webhooks(self) -> tuple[ProviderWebhook, ...]:
            raise ProviderPermanentError(operation="list_webhooks")

    def fixed_client(settings: Settings) -> FailingGateway:
        return FailingGateway()

    monkeypatch.setattr(cli_module, "build_client", fixed_client)

    # When
    result = CliRunner().invoke(cli_module.app, ["provider", "list-webhooks"])

    # Then
    assert result.exit_code == 1
    assert json.loads(result.stderr) == {
        "error": "provider_operation_failed",
        "kind": "permanent",
        "operation": "list_webhooks",
    }


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (ProviderUnknownOutcomeError(operation="create_invoice"), "unknown_outcome"),
        (ProviderPermanentError(operation="create_invoice"), "permanent"),
    ],
)
def test_provider_failure_kind_is_sanitized(
    error: ProviderPermanentError | ProviderUnknownOutcomeError,
    kind: str,
) -> None:
    assert cli_module.provider_error_kind(error) == kind


def test_provider_smoke_invoice_requires_explicit_live_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setenv("STARKBANK_SANDBOX_LIVE_ENABLED", "false")

    # When
    result = CliRunner().invoke(
        cli_module.app,
        ["provider", "smoke-invoice", "--confirm-sandbox"],
    )

    # Then
    assert result.exit_code == 2


def test_provider_smoke_invoice_creates_deterministic_safe_invoice(
    monkeypatch: pytest.MonkeyPatch,
    services: Services,
) -> None:
    # Given
    gateway = FakeGateway()
    monkeypatch.setenv("STARKBANK_SANDBOX_LIVE_ENABLED", "true")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "starkbank-trial-local")

    def fixed_client(settings: Settings) -> FakeGateway:
        return gateway

    def fixed_store() -> InvoiceStore:
        return services.stores.invoices

    monkeypatch.setattr(cli_module, "build_client", fixed_client)
    monkeypatch.setattr(cli_module, "_invoice_store", fixed_store)

    # When
    result = CliRunner().invoke(
        cli_module.app,
        [
            "provider",
            "smoke-invoice",
            "--confirm-sandbox",
            "--reference",
            "smoke-1",
            "--amount-cents",
            "10000",
        ],
    )

    # Then
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["reused"] is False
    assert payload["invoice_id"].startswith("invoice-")
    assert len(gateway.created_drafts) == 1
    assert gateway.created_drafts[0].amount == 10_000


def test_provider_cleanup_events_deletes_events_in_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    class CleanupEventsGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.deleted: list[str] = []

        def list_events(
            self,
            after: datetime,
            before: datetime | None = None,
        ) -> tuple[EventId, ...]:
            return tuple(EventId(f"event-{ordinal}") for ordinal in range(3))

        def delete_event(self, event_id: EventId) -> None:
            self.deleted.append(str(event_id))

    gateway = CleanupEventsGateway()
    monkeypatch.setenv("STARKBANK_SANDBOX_LIVE_ENABLED", "true")

    def fixed_client(settings: Settings) -> CleanupEventsGateway:
        return gateway

    monkeypatch.setattr(cli_module, "build_client", fixed_client)

    # When
    result = CliRunner().invoke(
        cli_module.app,
        [
            "provider",
            "cleanup-events",
            "--after",
            "2026-08-07T08:00:00+00:00",
            "--confirm-sandbox",
        ],
    )

    # Then
    assert result.exit_code == 0
    assert gateway.deleted == ["event-0", "event-1", "event-2"]
    assert json.loads(result.stdout) == {
        "deleted": ["event-0", "event-1", "event-2"],
        "failed": [],
        "retry_cancellation_guaranteed": False,
        "window": {"after": "2026-08-07T08:00:00+00:00", "before": None},
    }


def test_provider_cleanup_events_reports_partial_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    class FailingDeleteGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.deleted: list[str] = []

        def list_events(
            self,
            after: datetime,
            before: datetime | None = None,
        ) -> tuple[EventId, ...]:
            return tuple(EventId(f"event-{ordinal}") for ordinal in range(3))

        def delete_event(self, event_id: EventId) -> None:
            if str(event_id) == "event-1":
                raise ProviderPermanentError(operation="delete_event")
            self.deleted.append(str(event_id))

    gateway = FailingDeleteGateway()
    monkeypatch.setenv("STARKBANK_SANDBOX_LIVE_ENABLED", "true")

    def fixed_client(settings: Settings) -> FailingDeleteGateway:
        return gateway

    monkeypatch.setattr(cli_module, "build_client", fixed_client)

    # When
    result = CliRunner().invoke(
        cli_module.app,
        [
            "provider",
            "cleanup-events",
            "--after",
            "2026-08-07T08:00:00+00:00",
            "--before",
            "2026-08-07T09:00:00+00:00",
            "--confirm-sandbox",
        ],
    )

    # Then
    assert result.exit_code == 0
    assert gateway.deleted == ["event-0", "event-2"]
    assert json.loads(result.stdout) == {
        "deleted": ["event-0", "event-2"],
        "failed": ["event-1"],
        "retry_cancellation_guaranteed": False,
        "window": {
            "after": "2026-08-07T08:00:00+00:00",
            "before": "2026-08-07T09:00:00+00:00",
        },
    }


def test_provider_cleanup_events_requires_after_and_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setenv("STARKBANK_SANDBOX_LIVE_ENABLED", "true")

    # When / Then: missing --after is rejected
    missing_after = CliRunner().invoke(
        cli_module.app,
        ["provider", "cleanup-events", "--confirm-sandbox"],
    )
    assert missing_after.exit_code == 2

    # When / Then: missing --confirm-sandbox is rejected
    monkeypatch.setenv("STARKBANK_SANDBOX_LIVE_ENABLED", "false")
    without_confirm = CliRunner().invoke(
        cli_module.app,
        ["provider", "cleanup-events", "--after", "2026-08-07T08:00:00+00:00"],
    )
    assert without_confirm.exit_code == 2

    # When / Then: invalid ISO timestamp is rejected
    monkeypatch.setenv("STARKBANK_SANDBOX_LIVE_ENABLED", "true")
    invalid_after = CliRunner().invoke(
        cli_module.app,
        [
            "provider",
            "cleanup-events",
            "--after",
            "not-a-timestamp",
            "--confirm-sandbox",
        ],
    )
    assert invalid_after.exit_code == 2


def test_provider_cleanup_events_list_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    class FailingListGateway(FakeGateway):
        def list_events(
            self,
            after: datetime,
            before: datetime | None = None,
        ) -> tuple[EventId, ...]:
            raise ProviderPermanentError(operation="list_events")

    monkeypatch.setenv("STARKBANK_SANDBOX_LIVE_ENABLED", "true")

    def fixed_client(settings: Settings) -> FailingListGateway:
        return FailingListGateway()

    monkeypatch.setattr(cli_module, "build_client", fixed_client)

    # When
    result = CliRunner().invoke(
        cli_module.app,
        [
            "provider",
            "cleanup-events",
            "--after",
            "2026-08-07T08:00:00+00:00",
            "--confirm-sandbox",
        ],
    )

    # Then
    assert result.exit_code == 1
    assert json.loads(result.stderr) == {
        "error": "provider_operation_failed",
        "kind": "permanent",
        "operation": "list_events",
    }


def test_db_upgrade_command_applies_migrations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given
    database = tmp_path / "migration.db"
    database_url = f"sqlite+pysqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    # When
    result = CliRunner().invoke(cli_module.app, ["db", "upgrade"])

    # Then
    assert result.exit_code == 0
    engine = create_engine(database_url)
    assert "transfer_jobs" in inspect(engine).get_table_names()
    assert "owned_invoices" in inspect(engine).get_table_names()
    engine.dispose()


def test_direct_alembic_upgrade_reads_project_env_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given
    database = tmp_path / "direct-migration.db"
    database_url = f"sqlite+pysqlite:///{database}"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    (tmp_path / ".env").write_text(f"DATABASE_URL={database_url}\n", encoding="utf-8")
    repository = Path(__file__).parents[1]
    config = AlembicConfig(str(repository / "alembic.ini"))
    config.set_main_option("script_location", str(repository / "migrations"))

    # When
    alembic_command.upgrade(config, "head")

    # Then
    engine = create_engine(database_url)
    assert "transfer_jobs" in inspect(engine).get_table_names()
    assert "owned_invoices" in inspect(engine).get_table_names()
    engine.dispose()


def test_owned_invoice_migration_backfills_trial_and_legacy_smoke(
    tmp_path: Path,
) -> None:
    database = tmp_path / "backfill.db"
    database_url = f"sqlite+pysqlite:///{database}"
    repository = Path(__file__).parents[1]
    config = AlembicConfig(str(repository / "alembic.ini"))
    config.set_main_option("script_location", str(repository / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    alembic_command.upgrade(config, "0003_transfer_lifecycle")
    now = "2026-08-08 12:00:00"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO trial_runs "
                "(id, status, started_at, ends_at, created_at, completed_at, active_marker) "
                "VALUES (:id, 'running', :now, :now, :now, NULL, NULL)"
            ),
            {"id": "trial-run-000000000000000000000000000000", "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO trial_runs "
                "(id, status, started_at, ends_at, created_at, completed_at, active_marker) "
                "VALUES (:id, 'completed', :now, :now, :now, :now, NULL)"
            ),
            {"id": "smoke-run-0000000000000000000000000000", "now": now},
        )
        for batch_id, run_id in (
            ("trial-batch-000000000000000000000000000", "trial-run-000000000000000000000000000000"),
            ("sandbox-smoke", "smoke-run-0000000000000000000000000000"),
        ):
            connection.execute(
                text(
                    "INSERT INTO invoice_batches "
                    "(id, run_id, slot_index, scheduled_at, target_count, status, attempts, "
                    "created_at, completed_at) VALUES (:id, :run_id, 0, :now, 8, 'completed', "
                    "0, :now, :now)"
                ),
                {"id": batch_id, "run_id": run_id, "now": now},
            )
        for draft_id, batch_id, tag, provider_id in (
            (
                "trial-draft-000000000000000000000000000",
                "trial-batch-000000000000000000000000000",
                "trial-tag",
                "invoice-trial",
            ),
            (
                "smoke-draft-000000000000000000000000000",
                "sandbox-smoke",
                "smoke-tag",
                "invoice-smoke",
            ),
        ):
            connection.execute(
                text(
                    "INSERT INTO invoice_drafts "
                    "(id, batch_id, ordinal, payer_name, payer_tax_id, amount, tag, status, "
                    "provider_invoice_id, attempts, reconcile_attempts, last_error_code, "
                    "created_at, updated_at, next_attempt_at) VALUES "
                    "(:id, :batch_id, 0, 'Payer', '123', 100, :tag, 'created', :provider_id, "
                    "0, 0, NULL, :now, :now, :now)"
                ),
                {
                    "id": draft_id,
                    "batch_id": batch_id,
                    "tag": tag,
                    "provider_id": provider_id,
                    "now": now,
                },
            )
    alembic_command.upgrade(config, "head")
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT tag, provider_invoice_id, source, draft_id FROM owned_invoices ORDER BY tag"
            )
        ).all()
    assert rows == [
        ("smoke-tag", "invoice-smoke", "smoke", "smoke-draft-000000000000000000000000000"),
        ("trial-tag", "invoice-trial", "trial", "trial-draft-000000000000000000000000000"),
    ]
    engine.dispose()


def test_should_poll_only_for_no_work_results() -> None:
    assert should_poll(WorkerResult.SUCCEEDED) is False
    assert should_poll(WorkerResult.RETRY_SCHEDULED) is False
    assert should_poll(WorkerResult.RECONCILIATION_SCHEDULED) is False
    assert should_poll(WorkerResult.PERMANENT_FAILURE) is False
    assert should_poll(WorkerResult.IDLE) is True
    assert should_poll(WorkerResult.LIVE_DISABLED) is True


def test_worker_run_sleeps_when_live_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    class DisabledWorker:
        def __init__(self) -> None:
            self.count = 0

        def process_one(self) -> WorkerResult:
            self.count += 1
            if self.count > 1:
                raise KeyboardInterrupt
            return WorkerResult.LIVE_DISABLED

    class DisabledServices:
        worker = DisabledWorker()
        provider = None

    monkeypatch.setattr(cli_module, "_services", DisabledServices)
    calls: list[float] = []
    monkeypatch.setattr(cli_module.time, "sleep", calls.append)

    # When
    with pytest.raises(KeyboardInterrupt):
        cli_module.worker_run(poll_seconds=2.0)

    # Then
    assert calls == [2.0]
