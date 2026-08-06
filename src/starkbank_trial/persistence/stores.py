from dataclasses import dataclass

from sqlalchemy import Engine

from starkbank_trial.persistence.event_store import EventStore
from starkbank_trial.persistence.invoice_store import InvoiceStore
from starkbank_trial.persistence.transfer_store import TransferStore
from starkbank_trial.persistence.trial_store import TrialStore


@dataclass(frozen=True, slots=True)
class Stores:
    trials: TrialStore
    invoices: InvoiceStore
    events: EventStore
    transfers: TransferStore


def build_stores(engine: Engine) -> Stores:
    return Stores(
        trials=TrialStore(engine),
        invoices=InvoiceStore(engine),
        events=EventStore(engine),
        transfers=TransferStore(engine),
    )
