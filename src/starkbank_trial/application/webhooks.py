from dataclasses import dataclass
from hashlib import sha256

from starkbank_trial.application.clock import Clock
from starkbank_trial.domain.errors import InvalidAmountError
from starkbank_trial.domain.events import (
    CreditedInvoiceEvent,
    EventRecord,
    EventWriteResult,
    IgnoredEvent,
)
from starkbank_trial.domain.money import calculate_net_amount
from starkbank_trial.domain.provider import (
    UnexpectedWorkspaceError,
    WebhookVerifier,
)
from starkbank_trial.domain.types import EventId
from starkbank_trial.persistence.event_store import EventStore


@dataclass(frozen=True, slots=True)
class WebhookService:
    verifier: WebhookVerifier
    events: EventStore
    clock: Clock

    def receive(self, content: bytes, signature: str) -> EventWriteResult:
        try:
            event = self.verifier.verify_event(content, signature)
        except UnexpectedWorkspaceError as error:
            self.events.record_ignored_workspace(
                EventRecord(
                    event=IgnoredEvent(
                        event_id=EventId(error.event_id),
                        subscription=error.subscription,
                        log_type=error.log_type,
                        workspace_id=error.workspace_id,
                    ),
                    payload_hash=sha256(content).hexdigest(),
                    received_at=self.clock.now(),
                    net_amount=None,
                )
            )
            raise
        net_amount = None
        if isinstance(event, CreditedInvoiceEvent):
            try:
                net_amount = calculate_net_amount(event.amount, event.fee)
            except InvalidAmountError:
                net_amount = None
        return self.events.record(
            EventRecord(
                event=event,
                payload_hash=sha256(content).hexdigest(),
                received_at=self.clock.now(),
                net_amount=net_amount,
            )
        )
