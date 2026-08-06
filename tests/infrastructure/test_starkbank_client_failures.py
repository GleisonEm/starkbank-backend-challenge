from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import starkbank
from starkbank.error import InputErrors, InvalidSignatureError, UnknownError

from starkbank_trial.domain.events import IgnoredEvent
from starkbank_trial.domain.invoices import InvoiceDraft
from starkbank_trial.domain.provider import (
    InvalidWebhookError,
    ProviderPermanentError,
    ProviderTimeoutError,
    ProviderTransientError,
)
from starkbank_trial.domain.status import DraftStatus
from starkbank_trial.domain.transfer import build_transfer_command
from starkbank_trial.domain.types import BatchId, Cents, DraftId, InvoiceId, TransferId
from starkbank_trial.infrastructure.starkbank_client import StarkBankClient


@pytest.fixture
def sdk_client() -> StarkBankClient:
    private_key, _ = starkbank.key.create()
    return StarkBankClient.from_credentials("project-1", private_key)


def draft() -> InvoiceDraft:
    return InvoiceDraft(
        DraftId("draft-1"),
        BatchId("batch-1"),
        0,
        "Maria Silva",
        "52998224725",
        Cents(4_250),
        "trial-invoice:draft-1",
        DraftStatus.PENDING,
        0,
        datetime(2026, 8, 1, 12, tzinfo=UTC),
    )


def test_create_invoice_timeout_has_unknown_outcome(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    # Given
    def create(
        invoices: list[starkbank.Invoice],
        user: starkbank.Project | None = None,
    ) -> list[starkbank.Invoice]:
        message = "timeout"
        raise UnknownError(message)

    monkeypatch.setattr(starkbank.invoice, "create", create)

    # When / Then
    with pytest.raises(ProviderTimeoutError) as captured:
        sdk_client.create_invoice(draft())
    assert str(captured.value) == "provider operation failed: create_invoice"


def test_read_timeout_is_transient(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    # Given
    def query(
        limit: int | None = None,
        tags: list[str] | None = None,
        user: starkbank.Project | None = None,
    ) -> Iterator[starkbank.Invoice]:
        message = "timeout"
        raise UnknownError(message)

    monkeypatch.setattr(starkbank.invoice, "query", query)

    # When / Then
    with pytest.raises(ProviderTransientError):
        sdk_client.find_invoice("trial-invoice:draft-1")


def test_invalid_sdk_response_is_permanent(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    # Given
    def create(
        invoices: list[starkbank.Invoice],
        user: starkbank.Project | None = None,
    ) -> list[starkbank.Invoice]:
        return [starkbank.Invoice(amount=1_000, tax_id="52998224725", name="Maria")]

    monkeypatch.setattr(starkbank.invoice, "create", create)

    # When / Then
    with pytest.raises(ProviderPermanentError):
        sdk_client.create_invoice(draft())


def test_duplicate_transfer_reconciles_by_external_id(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    # Given
    command = build_transfer_command(InvoiceId("invoice-1"), Cents(9_950))
    transfer = starkbank.Transfer(
        amount=9_950,
        name="Stark Bank S.A.",
        tax_id="20.018.183/0001-80",
        bank_code="20018183",
        branch_code="0001",
        account_number="6341320293482496",
        account_type="payment",
        external_id=command.external_id,
        tags=[command.tag],
    )
    transfer.id = "transfer-existing"
    transfer.status = "created"
    query_results = iter((iter(()), iter((transfer,))))

    def query(
        limit: int | None = None,
        tags: list[str] | None = None,
        user: starkbank.Project | None = None,
    ) -> Iterator[starkbank.Transfer]:
        return next(query_results)

    def create(
        transfers: list[starkbank.Transfer],
        user: starkbank.Project | None = None,
    ) -> list[starkbank.Transfer]:
        raise InputErrors([{"code": "invalid", "message": "duplicated external ID"}])

    monkeypatch.setattr(starkbank.transfer, "query", query)
    monkeypatch.setattr(starkbank.transfer, "create", create)

    # When
    result = sdk_client.ensure_transfer(command)

    # Then
    assert result.id == TransferId("transfer-existing")


def test_webhook_maps_ignored_and_invalid_events(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    # Given
    responses: Iterator[object] = iter(
        (
            SimpleNamespace(
                id="event-1",
                subscription="transfer",
                workspace_id="workspace-1",
                log=SimpleNamespace(type="created"),
            ),
            InvalidSignatureError("invalid"),
            UnknownError("unavailable"),
        )
    )

    def parse(
        content: str,
        signature: str,
        user: starkbank.Project | None = None,
    ) -> object:
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(starkbank.event, "parse", parse)

    # When
    ignored = sdk_client.verify_event(b"payload", "signature")

    # Then
    assert isinstance(ignored, IgnoredEvent)
    with pytest.raises(InvalidWebhookError):
        sdk_client.verify_event(b"payload", "signature")
    with pytest.raises(ProviderTransientError):
        sdk_client.verify_event(b"payload", "signature")
