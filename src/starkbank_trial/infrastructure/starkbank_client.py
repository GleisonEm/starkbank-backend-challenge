from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NoReturn, cast

if TYPE_CHECKING:
    from collections.abc import Callable

import starkbank
from pydantic import BaseModel, ConfigDict, ValidationError
from starkbank.error import (
    InputErrors,
    InternalServerError,
    InvalidSignatureError,
    StarkError,
    UnknownError,
)

from starkbank_trial.domain.constants import WEBHOOK_SUBSCRIPTIONS
from starkbank_trial.domain.errors import LiveOperationsDisabledError
from starkbank_trial.domain.events import (
    CreditedInvoiceEvent,
    IgnoredEvent,
    TransferLifecycleEvent,
    VerifiedEvent,
)
from starkbank_trial.domain.invoices import InvoiceDraft, ProviderInvoice
from starkbank_trial.domain.models import TransferCommand
from starkbank_trial.domain.provider import (
    InvalidWebhookError,
    ProviderPermanentError,
    ProviderTransfer,
    ProviderTransientError,
    ProviderUnknownOutcomeError,
    ProviderWebhook,
    UnexpectedWorkspaceError,
    WebhookInspection,
)
from starkbank_trial.domain.types import Cents, EventId, ExternalId, InvoiceId, TransferId


class _SdkInvoice(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str


class _SdkTransfer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    external_id: str
    status: str


class _SdkTransferLifecycle(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    external_id: str
    status: str
    updated: datetime


class _SdkEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    subscription: str
    workspace_id: str
    log: object


class _SdkLog(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: str


class _SdkCreditedLog(_SdkLog):
    invoice: object


class _SdkTransferLog(_SdkLog):
    transfer: object


class _SdkCreditedInvoice(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    amount: int
    fee: int


class _SdkWebhook(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    url: str
    subscriptions: list[str]


@dataclass(frozen=True, slots=True)
class StarkBankClient:
    user: starkbank.Project
    expected_workspace_id: str | None = None
    live_operations_enabled: bool = False

    @classmethod
    def from_credentials(
        cls,
        project_id: str,
        private_key: str,
        *,
        expected_workspace_id: str | None = None,
        live_operations_enabled: bool = False,
    ) -> "StarkBankClient":
        return cls(
            starkbank.Project(project_id, "sandbox", private_key),
            expected_workspace_id=expected_workspace_id,
            live_operations_enabled=live_operations_enabled,
        )

    def create_invoice(self, draft: InvoiceDraft) -> ProviderInvoice:
        self._require_live("create_invoice")
        request = starkbank.Invoice(
            amount=draft.amount,
            tax_id=draft.payer_tax_id,
            name=draft.payer_name,
            tags=[draft.tag],
        )
        try:
            created = starkbank.invoice.create([request], user=self.user)
            invoice = _SdkInvoice.model_validate(created[0])
        except IndexError as error:
            raise ProviderUnknownOutcomeError(operation="create_invoice_empty") from error
        except (StarkError, ValidationError) as error:
            self._raise_provider(error, "create_invoice", unknown_outcome=True)
        return ProviderInvoice(id=InvoiceId(invoice.id), tag=draft.tag)

    def find_invoice(self, tag: str) -> ProviderInvoice | None:
        try:
            invoices = starkbank.invoice.query(limit=1, tags=[tag], user=self.user)
            invoice = next(invoices, None)
            if invoice is None:
                return None
            parsed = _SdkInvoice.model_validate(invoice)
        except (StarkError, ValidationError) as error:
            self._raise_provider(error, "find_invoice", unknown_outcome=False)
        return ProviderInvoice(id=InvoiceId(parsed.id), tag=tag)

    def ensure_transfer(self, command: TransferCommand) -> ProviderTransfer:
        self._require_live("ensure_transfer")
        existing = self._find_transfer(command)
        if existing is not None:
            return existing
        recipient = command.recipient
        request = starkbank.Transfer(
            amount=command.amount,
            name=recipient.name,
            tax_id=recipient.tax_id,
            bank_code=recipient.bank_code,
            branch_code=recipient.branch_code,
            account_number=recipient.account_number,
            account_type=recipient.account_type,
            external_id=command.external_id,
            tags=[command.tag],
            description="Stark Bank backend trial invoice credit",
        )
        try:
            created = starkbank.transfer.create([request], user=self.user)
            transfer = _SdkTransfer.model_validate(created[0])
        except IndexError as error:
            raise ProviderUnknownOutcomeError(operation="create_transfer_empty") from error
        except InputErrors as error:
            reconciled = self._find_transfer(command)
            if reconciled is not None:
                return reconciled
            raise ProviderPermanentError(operation="create_transfer") from error
        except (StarkError, ValidationError) as error:
            self._raise_provider(error, "create_transfer", unknown_outcome=True)
        return self._provider_transfer(transfer)

    def find_transfer(self, command: TransferCommand) -> ProviderTransfer | None:
        return self._find_transfer(command)

    def verify_event(self, content: bytes, signature: str) -> VerifiedEvent:
        try:
            raw_event = starkbank.event.parse(content.decode(), signature, user=self.user)
            event = _SdkEvent.model_validate(raw_event)
            log = _SdkLog.model_validate(event.log)
            self._assert_expected_workspace(event, log)
            if event.subscription == "transfer":
                try:
                    transfer_log = _SdkTransferLog.model_validate(event.log)
                except ValidationError:
                    return IgnoredEvent(
                        event_id=EventId(event.id),
                        subscription=event.subscription,
                        log_type=log.type,
                        workspace_id=event.workspace_id,
                    )
                transfer = _SdkTransferLifecycle.model_validate(transfer_log.transfer)
                updated_at = transfer.updated
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=UTC)
                return TransferLifecycleEvent(
                    event_id=EventId(event.id),
                    transfer_id=TransferId(transfer.id),
                    external_id=ExternalId(transfer.external_id),
                    status=transfer.status,
                    log_type=log.type,
                    updated_at=updated_at,
                    workspace_id=event.workspace_id,
                )
            if event.subscription != "invoice" or log.type != "credited":
                return IgnoredEvent(
                    event_id=EventId(event.id),
                    subscription=event.subscription,
                    log_type=log.type,
                    workspace_id=event.workspace_id,
                )
            credited_log = _SdkCreditedLog.model_validate(event.log)
            invoice = _SdkCreditedInvoice.model_validate(credited_log.invoice)
        except UnexpectedWorkspaceError:
            raise
        except (UnicodeDecodeError, InvalidSignatureError, ValidationError) as error:
            raise InvalidWebhookError(operation="verify_event") from error
        except (InternalServerError, UnknownError) as error:
            raise ProviderTransientError(operation="verify_event") from error
        return CreditedInvoiceEvent(
            event_id=EventId(event.id),
            invoice_id=InvoiceId(invoice.id),
            amount=Cents(invoice.amount),
            fee=Cents(invoice.fee),
            workspace_id=event.workspace_id,
        )

    def ensure_webhook(self, url: str) -> ProviderWebhook:
        self._require_live("ensure_webhook")
        inspection = self.inspect_webhooks(url)
        if inspection.active is not None:
            return inspection.active
        for stale in inspection.stale:
            self.delete_webhook(stale.id)
        try:
            created = starkbank.webhook.create(url, list(WEBHOOK_SUBSCRIPTIONS), user=self.user)
            webhook = _SdkWebhook.model_validate(created)
        except (StarkError, ValidationError) as error:
            self._raise_provider(error, "create_webhook", unknown_outcome=True)
        return self._confirm_webhook(self._provider_webhook(webhook), url)

    def inspect_webhooks(self, url: str) -> WebhookInspection:
        matching = [webhook for webhook in self.list_webhooks() if webhook.url == url]
        active = next(
            (
                webhook
                for webhook in matching
                if set(webhook.subscriptions) == set(WEBHOOK_SUBSCRIPTIONS)
            ),
            None,
        )
        stale = tuple(webhook for webhook in matching if webhook is not active)
        return WebhookInspection(active=active, stale=stale)

    def _confirm_webhook(self, webhook: ProviderWebhook, url: str) -> ProviderWebhook:
        inspection = self.inspect_webhooks(url)
        if inspection.active is not None and inspection.active.id == webhook.id:
            return inspection.active
        raise ProviderUnknownOutcomeError(operation="create_webhook_unconfirmed")

    def delete_webhook(self, webhook_id: str) -> None:
        self._require_live("delete_webhook")
        delete = cast("Callable[..., object] | None", getattr(starkbank.webhook, "delete", None))
        if delete is None:
            raise ProviderPermanentError(operation="delete_webhook")
        try:
            delete(webhook_id, user=self.user)
        except StarkError as error:
            self._raise_provider(error, "delete_webhook", unknown_outcome=True)

    def list_webhooks(self) -> tuple[ProviderWebhook, ...]:
        try:
            return tuple(
                self._provider_webhook(_SdkWebhook.model_validate(item))
                for item in starkbank.webhook.query(user=self.user)
            )
        except (StarkError, ValidationError) as error:
            self._raise_provider(error, "list_webhooks", unknown_outcome=False)

    def _find_transfer(self, command: TransferCommand) -> ProviderTransfer | None:
        try:
            transfers = starkbank.transfer.query(
                limit=10,
                tags=[command.tag],
                user=self.user,
            )
            for item in transfers:
                parsed = _SdkTransfer.model_validate(item)
                if parsed.external_id == command.external_id:
                    return self._provider_transfer(parsed)
        except (StarkError, ValidationError) as error:
            self._raise_provider(error, "find_transfer", unknown_outcome=False)
        return None

    @staticmethod
    def _provider_transfer(transfer: _SdkTransfer) -> ProviderTransfer:
        return ProviderTransfer(
            id=TransferId(transfer.id),
            external_id=ExternalId(transfer.external_id),
            status=transfer.status,
        )

    @staticmethod
    def _provider_webhook(webhook: _SdkWebhook) -> ProviderWebhook:
        return ProviderWebhook(webhook.id, webhook.url, tuple(webhook.subscriptions))

    def _require_live(self, operation: str) -> None:
        if not self.live_operations_enabled:
            raise LiveOperationsDisabledError(operation=operation)

    def _assert_expected_workspace(self, event: _SdkEvent, log: _SdkLog) -> None:
        if (
            self.expected_workspace_id is not None
            and event.workspace_id != self.expected_workspace_id
        ):
            raise UnexpectedWorkspaceError(
                operation="verify_event",
                workspace_id=event.workspace_id,
                event_id=event.id,
                subscription=event.subscription,
                log_type=log.type,
            )

    @staticmethod
    def _raise_provider(error: Exception, operation: str, *, unknown_outcome: bool) -> NoReturn:
        if unknown_outcome and isinstance(
            error, (InternalServerError, UnknownError, ValidationError)
        ):
            raise ProviderUnknownOutcomeError(operation=operation) from error
        if isinstance(error, (InternalServerError, UnknownError)):
            raise ProviderTransientError(operation=operation) from error
        raise ProviderPermanentError(operation=operation) from error
