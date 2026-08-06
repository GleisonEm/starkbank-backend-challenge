import json
import time
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, NoReturn
from uuid import NAMESPACE_URL, uuid5

import typer
from alembic import command
from alembic.config import Config

from starkbank_trial.application.payers import build_smoke_invoice
from starkbank_trial.bootstrap import Services, build_client, build_services
from starkbank_trial.config import Settings
from starkbank_trial.domain.provider import ProviderError
from starkbank_trial.logging import configure_logging

app = typer.Typer(no_args_is_help=True)
db_app = typer.Typer(no_args_is_help=True)
trial_app = typer.Typer(no_args_is_help=True)
worker_app = typer.Typer(no_args_is_help=True)
provider_app = typer.Typer(no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(trial_app, name="trial")
app.add_typer(worker_app, name="worker")
app.add_typer(provider_app, name="provider")


def _services() -> Services:
    settings = Settings()
    configure_logging(settings.log_level, settings.log_file)
    return build_services(settings)


def _write_result(result: StrEnum | dict[str, str | int]) -> None:
    payload = {"result": result.value} if isinstance(result, StrEnum) else result
    typer.echo(json.dumps(payload, sort_keys=True))


def _exit_provider_error(error: ProviderError) -> NoReturn:
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
    trial = _services().trial.start(parsed)
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
    report = _services().stores.trials.report()
    trial = report.trial
    trial_payload = (
        None
        if trial is None
        else {
            "id": trial.id,
            "status": trial.status.value,
            "started_at": trial.started_at.isoformat(),
            "ends_at": trial.ends_at.isoformat(),
            "next_batch_at": (
                trial.next_batch_at.isoformat() if trial.next_batch_at is not None else None
            ),
        }
    )
    typer.echo(
        json.dumps(
            {
                "trial": trial_payload,
                "batches": {item.status: item.count for item in report.batches},
                "invoices": {item.status: item.count for item in report.invoices},
                "webhook_events": {item.status: item.count for item in report.webhook_events},
                "transfers": {item.status: item.count for item in report.transfers},
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
        webhook = build_client(settings).ensure_webhook(url)
    except ProviderError as error:
        _exit_provider_error(error)
    _write_result({"webhook_id": webhook.id, "url": webhook.url})


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
    except ProviderError as error:
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


@worker_app.command("once")
def worker_once() -> None:
    _write_result(_services().worker.process_one())


@worker_app.command("run")
def worker_run(poll_seconds: float = typer.Option(default=1.0, min=0.1)) -> None:
    services = _services()
    while True:
        result = services.worker.process_one()
        if result.value == "idle":
            time.sleep(poll_seconds)
