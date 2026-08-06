from dataclasses import dataclass

from sqlalchemy import Engine

from starkbank_trial.application.clock import SystemClock
from starkbank_trial.application.transfers import TransferWorker
from starkbank_trial.application.trials import SecureBatchCounts, TrialService
from starkbank_trial.application.webhooks import WebhookService
from starkbank_trial.config import Settings
from starkbank_trial.infrastructure.starkbank_client import StarkBankClient
from starkbank_trial.persistence.engine import build_engine
from starkbank_trial.persistence.stores import Stores, build_stores


@dataclass(frozen=True, slots=True)
class Services:
    engine: Engine
    stores: Stores
    trial: TrialService
    webhook: WebhookService
    worker: TransferWorker


def build_services(settings: Settings) -> Services:
    client = build_client(settings)
    engine = build_engine(settings.database_url)
    stores = build_stores(engine)
    clock = SystemClock()
    return Services(
        engine=engine,
        stores=stores,
        trial=TrialService(
            stores,
            client,
            clock,
            SecureBatchCounts(),
            batch_lease_seconds=settings.batch_lease_seconds,
        ),
        webhook=WebhookService(client, stores.events, clock),
        worker=TransferWorker(
            stores.transfers,
            client,
            clock,
            lease_seconds=settings.worker_lease_seconds,
            retry_base_seconds=settings.retry_base_seconds,
        ),
    )


def build_client(settings: Settings) -> StarkBankClient:
    provider = settings.provider_credentials()
    private_key = provider.private_key_file.read_text(encoding="utf-8")
    return StarkBankClient.from_credentials(provider.project_id, private_key)
