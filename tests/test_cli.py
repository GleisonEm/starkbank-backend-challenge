import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect
from typer.testing import CliRunner

import starkbank_trial.cli as cli_module
from starkbank_trial.application.clock import SystemClock
from starkbank_trial.application.transfers import TransferWorker
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
    ProviderWebhook,
)
from starkbank_trial.domain.types import EventId, InvoiceId, TransferId
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


def test_provider_cleanup_webhooks_keeps_only_invoice_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CleanupGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.deleted: list[str] = []

        def list_webhooks(self) -> tuple[ProviderWebhook, ...]:
            return (
                ProviderWebhook(
                    id="webhook-old",
                    url="https://old.example.com/webhooks/starkbank",
                    subscriptions=("invoice",),
                ),
                ProviderWebhook(
                    id="webhook-wrong",
                    url="https://trial.example.com/webhooks/starkbank",
                    subscriptions=("invoice", "transfer"),
                ),
            )

        def delete_webhook(self, webhook_id: str) -> None:
            self.deleted.append(webhook_id)

        def ensure_webhook(self, url: str) -> ProviderWebhook:
            return ProviderWebhook("webhook-new", url, ("invoice",))

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
    assert gateway.deleted == ["webhook-old", "webhook-wrong"]
    assert json.loads(result.stdout) == {
        "active_url": "https://trial.example.com/webhooks/starkbank",
        "removed": 2,
        "subscription": "invoice",
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
        "operation": "list_webhooks",
    }


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
) -> None:
    # Given
    gateway = FakeGateway()
    monkeypatch.setenv("STARKBANK_SANDBOX_LIVE_ENABLED", "true")

    def fixed_client(settings: Settings) -> FakeGateway:
        return gateway

    monkeypatch.setattr(cli_module, "build_client", fixed_client)

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
    engine.dispose()
