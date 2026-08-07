import json
import time
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, NoReturn, cast
from uuid import NAMESPACE_URL, uuid5

import structlog
import typer
from alembic import command
from alembic.config import Config

from starkbank_trial.application.clock import SystemClock
from starkbank_trial.application.payers import build_smoke_invoice
from starkbank_trial.application.smoke import SmokeBatchService
from starkbank_trial.application.transfers import should_poll
from starkbank_trial.bootstrap import Services, build_client, build_services
from starkbank_trial.config import Settings
from starkbank_trial.domain.constants import WEBHOOK_SUBSCRIPTIONS
from starkbank_trial.domain.errors import LiveOperationsDisabledError
from starkbank_trial.domain.provider import ProviderError
from starkbank_trial.domain.transfer import build_transfer_command
from starkbank_trial.logging import configure_logging
from starkbank_trial.persistence.review_store import ReviewStore

logger = structlog.get_logger()

app = typer.Typer(no_args_is_help=True)
db_app = typer.Typer(no_args_is_help=True)
trial_app = typer.Typer(no_args_is_help=True)
worker_app = typer.Typer(no_args_is_help=True)
provider_app = typer.Typer(no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(trial_app, name="trial")
app.add_typer(worker_app, name="worker")
app.add_typer(provider_app, name="provider")

SANDBOX_CONFIRMATION_ERROR = (
    "live Sandbox calls require STARKBANK_SANDBOX_LIVE_ENABLED=true and --confirm-sandbox"
)
INVALID_ISO_TIMESTAMP_ERROR = "after and before must be ISO 8601 timestamps"
INVALID_TIME_WINDOW_ERROR = "before must be later than after"


def _services() -> Services:
    settings = Settings()
    configure_logging(settings.log_level, settings.log_file)
    return build_services(settings)


def _write_result(result: StrEnum | dict[str, object]) -> None:
    payload = {"result": result.value} if isinstance(result, StrEnum) else result
    typer.echo(json.dumps(payload, sort_keys=True))


def _exit_provider_error(error: ProviderError | LiveOperationsDisabledError) -> NoReturn:
    typer.echo(
        json.dumps(
            {"error": "provider_operation_failed", "operation": error.operation},
            sort_keys=True,
        ),
        err=True,
    )
    raise typer.Exit(code=1)


@db_app.command("upgrade")
def db_upgrade() -> None:
    settings = Settings()
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")


@trial_app.command("start")
def trial_start(start_at: str | None = typer.Option(default=None)) -> None:
    parsed = datetime.fromisoformat(start_at) if start_at is not None else None
    try:
        trial = _services().trial.start(parsed)
    except LiveOperationsDisabledError as error:
        _exit_provider_error(error)
    _write_result(
        {
            "trial_id": trial.id,
            "status": trial.status.value,
            "started_at": trial.started_at.isoformat(),
            "ends_at": trial.ends_at.isoformat(),
        }
    )


@trial_app.command("tick")
def trial_tick() -> None:
    _write_result(_services().trial.tick())


@trial_app.command("reconcile")
def trial_reconcile() -> None:
    _write_result({"reconciled": _services().trial.reconcile_invoices()})


@trial_app.command("status")
def trial_status() -> None:
    overview = ReviewStore(_services().engine).overview()
    trial_payload = overview["trial"]
    counts = cast("dict[str, object]", overview["counts"])
    typer.echo(
        json.dumps(
            {
                "trial": trial_payload,
                "batches": counts["batches"],
                "invoices": counts["invoices"],
                "webhook_events": counts["webhook_events"],
                "transfers": counts["transfers"],
            },
            sort_keys=True,
        )
    )


@provider_app.command("setup-webhook")
def provider_setup_webhook() -> None:
    settings = Settings()
    webhook_config = settings.webhook_config()
    url = f"{str(webhook_config.public_base_url).rstrip('/')}/webhooks/starkbank"
    try:
        client = build_client(settings)
        before = client.inspect_webhooks(url)
        webhook = client.ensure_webhook(url)
    except (ProviderError, LiveOperationsDisabledError) as error:
        _exit_provider_error(error)
    result: dict[str, object] = {
        "webhook_id": webhook.id,
        "url": webhook.url,
        "subscriptions": sorted(WEBHOOK_SUBSCRIPTIONS),
    }
    if before.stale:
        result["replaced_webhook_ids"] = [stale.id for stale in before.stale]
        result["notice"] = (
            "the previous webhook for this URL was incomplete and was replaced; "
            "delivery resumes with the complete webhook"
        )
    _write_result(result)


@provider_app.command("list-webhooks")
def provider_list_webhooks() -> None:
    try:
        webhooks = build_client(Settings()).list_webhooks()
    except ProviderError as error:
        _exit_provider_error(error)
    typer.echo(
        json.dumps(
            {
                "webhooks": [
                    {
                        "id": webhook.id,
                        "url": webhook.url,
                        "subscriptions": list(webhook.subscriptions),
                    }
                    for webhook in webhooks
                ]
            },
            sort_keys=True,
        )
    )


@provider_app.command("sync-transfer-statuses")
def provider_sync_transfer_statuses(
    limit: int = typer.Option(default=100, min=1, max=500),
) -> None:
    services = _services()
    provider = services.provider
    if provider is None:
        message = "provider is unavailable"
        raise typer.BadParameter(message)
    refreshed = 0
    unresolved = 0
    for candidate in services.stores.transfers.sync_candidates(limit):
        command = build_transfer_command(candidate.invoice_id, candidate.net_amount)
        try:
            transfer = provider.find_transfer(command)
        except ProviderError:
            unresolved += 1
            continue
        if transfer is None:
            unresolved += 1
            continue
        services.stores.transfers.refresh_provider_status(
            candidate.id,
            transfer,
            datetime.now(UTC),
        )
        refreshed += 1
    _write_result({"refreshed": refreshed, "unresolved": unresolved})


@provider_app.command("cleanup-webhooks")
def provider_cleanup_webhooks(
    *,
    confirm_sandbox: Annotated[bool, typer.Option()] = False,
) -> None:
    settings = Settings()
    if not settings.starkbank_sandbox_live_enabled or not confirm_sandbox:
        raise typer.BadParameter(SANDBOX_CONFIRMATION_ERROR)
    webhook_config = settings.webhook_config()
    target_url = f"{str(webhook_config.public_base_url).rstrip('/')}/webhooks/starkbank"
    client = build_client(settings)
    try:
        inspection = client.inspect_webhooks(target_url)
        replaced: list[str] = []
        if inspection.active is None:
            replaced = [stale.id for stale in inspection.stale]
            active = client.ensure_webhook(target_url)
        else:
            active = inspection.active
    except (ProviderError, LiveOperationsDisabledError) as error:
        _exit_provider_error(error)
    _write_result(
        {
            "active_url": active.url,
            "active_id": active.id,
            "replaced_webhook_ids": replaced,
            "subscriptions": sorted(WEBHOOK_SUBSCRIPTIONS),
        }
    )


@provider_app.command("cleanup-events")
def provider_cleanup_events(
    *,
    after: str = typer.Option(...),
    before: str | None = typer.Option(default=None),
    confirm_sandbox: Annotated[bool, typer.Option()] = False,
) -> None:
    settings = Settings()
    if not settings.starkbank_sandbox_live_enabled or not confirm_sandbox:
        raise typer.BadParameter(SANDBOX_CONFIRMATION_ERROR)
    try:
        after_dt = datetime.fromisoformat(after)
        before_dt = datetime.fromisoformat(before) if before is not None else None
    except ValueError as error:
        raise typer.BadParameter(INVALID_ISO_TIMESTAMP_ERROR) from error
    if before_dt is not None and before_dt <= after_dt:
        raise typer.BadParameter(INVALID_TIME_WINDOW_ERROR)
    client = build_client(settings)
    deleted: list[str] = []
    failed: list[str] = []
    try:
        for event_id in client.list_events(after_dt, before_dt):
            try:
                client.delete_event(event_id)
            except ProviderError:
                failed.append(str(event_id))
                continue
            deleted.append(str(event_id))
    except ProviderError as error:
        _exit_provider_error(error)
    _write_result(
        {
            "deleted": deleted,
            "failed": failed,
            "window": {
                "after": after_dt.isoformat(),
                "before": before_dt.isoformat() if before_dt is not None else None,
            },
        }
    )


@provider_app.command("smoke-invoice")
def provider_smoke_invoice(
    *,
    confirm_sandbox: Annotated[bool, typer.Option()] = False,
    reference: str = typer.Option(default="smoke-1"),
    amount_cents: int = typer.Option(default=10_000, min=1_000, max=100_000),
) -> None:
    settings = Settings()
    if not settings.starkbank_sandbox_live_enabled or not confirm_sandbox:
        message = (
            "live Sandbox calls require STARKBANK_SANDBOX_LIVE_ENABLED=true and --confirm-sandbox"
        )
        raise typer.BadParameter(message)
    client = build_client(settings)
    stable_reference = str(uuid5(NAMESPACE_URL, reference))
    draft = build_smoke_invoice(stable_reference, amount_cents, datetime.now(UTC))
    try:
        existing = client.find_invoice(draft.tag)
        invoice = existing if existing is not None else client.create_invoice(draft)
    except (ProviderError, LiveOperationsDisabledError) as error:
        _exit_provider_error(error)
    typer.echo(
        json.dumps(
            {
                "invoice_id": invoice.id,
                "tag": invoice.tag,
                "reused": existing is not None,
            },
            sort_keys=True,
        )
    )


@provider_app.command("smoke-batch")
def provider_smoke_batch(
    *,
    count: int = typer.Option(default=8, min=1, max=12),
    confirm_sandbox: Annotated[bool, typer.Option()] = False,
    reference: str = typer.Option(default="smoke-batch-1"),
    amount_cents: int = typer.Option(default=10_000, min=1_000, max=100_000),
) -> None:
    settings = Settings()
    if not settings.starkbank_sandbox_live_enabled or not confirm_sandbox:
        raise typer.BadParameter(SANDBOX_CONFIRMATION_ERROR)
    client = build_client(settings)
    try:
        result = SmokeBatchService(client, SystemClock()).run(reference, count, amount_cents)
    except (ProviderError, LiveOperationsDisabledError) as error:
        _exit_provider_error(error)
    typer.echo(
        json.dumps(
            {
                "count": len(result.invoices),
                "reused": result.reused,
                "invoice_ids": [invoice.id for invoice in result.invoices],
                "reference": reference,
            },
            sort_keys=True,
        )
    )


@worker_app.command("once")
def worker_once() -> None:
    _write_result(_services().worker.process_one())


@worker_app.command("run")
def worker_run(poll_seconds: float = typer.Option(default=1.0, min=0.1)) -> None:
    services = _services()
    settings = Settings()
    if settings.starkbank_sandbox_live_enabled:
        webhook_config = settings.webhook_config()
        url = f"{str(webhook_config.public_base_url).rstrip('/')}/webhooks/starkbank"
        provider = services.provider
        if provider is not None:
            try:
                provider.ensure_webhook(url)
            except (ProviderError, LiveOperationsDisabledError) as error:
                logger.warning(
                    "webhook_preflight_failed",
                    operation=error.operation,
                    url=url,
                )
    while True:
        result = services.worker.process_one()
        if should_poll(result):
            time.sleep(poll_seconds)
