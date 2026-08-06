from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import starkbank

from starkbank_trial.domain.events import CreditedInvoiceEvent
from starkbank_trial.domain.invoices import InvoiceDraft
from starkbank_trial.domain.status import DraftStatus
from starkbank_trial.domain.transfer import build_transfer_command
from starkbank_trial.domain.types import BatchId, Cents, DraftId, InvoiceId, TransferId
from starkbank_trial.infrastructure.starkbank_client import StarkBankClient


@pytest.fixture
def sdk_client() -> StarkBankClient:
    private_key, _ = starkbank.key.create()
    return StarkBankClient.from_credentials("project-1", private_key)


def test_create_invoice_maps_draft_to_official_sdk(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    # Given
    captured: list[starkbank.Invoice] = []

    def create(
        invoices: list[starkbank.Invoice],
        user: starkbank.Project | None = None,
    ) -> list[starkbank.Invoice]:
        captured.extend(invoices)
        invoices[0].id = "invoice-1"
        return invoices

    monkeypatch.setattr(starkbank.invoice, "create", create)
    draft = InvoiceDraft(
        id=DraftId("draft-1"),
        batch_id=BatchId("batch-1"),
        ordinal=0,
        payer_name="Maria Silva",
        payer_tax_id="52998224725",
        amount=Cents(4_250),
        tag="trial-invoice:draft-1",
        status=DraftStatus.PENDING,
        attempts=0,
        created_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
    )

    # When
    result = sdk_client.create_invoice(draft)

    # Then
    assert result.id == InvoiceId("invoice-1")
    assert captured[0].amount == 4_250
    assert captured[0].tax_id == "52998224725"
    assert captured[0].tags == ["trial-invoice:draft-1"]


def test_verify_event_maps_credited_invoice_from_signed_sdk_event(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    # Given
    raw = SimpleNamespace(
        id="event-1",
        subscription="invoice",
        workspace_id="workspace-1",
        log=SimpleNamespace(
            type="credited",
            invoice=SimpleNamespace(id="invoice-1", amount=10_000, fee=50),
        ),
    )

    def parse(
        content: str,
        signature: str,
        user: starkbank.Project | None = None,
    ) -> object:
        return raw

    monkeypatch.setattr(starkbank.event, "parse", parse)

    # When
    event = sdk_client.verify_event(b'{"event": {}}', "valid-signature")

    # Then
    assert isinstance(event, CreditedInvoiceEvent)
    assert event.invoice_id == InvoiceId("invoice-1")
    assert event.amount == Cents(10_000)
    assert event.fee == Cents(50)


def test_ensure_transfer_uses_exact_recipient_and_stable_external_id(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    # Given
    captured: list[starkbank.Transfer] = []

    def query(
        limit: int | None = None,
        tags: list[str] | None = None,
        user: starkbank.Project | None = None,
    ) -> Iterator[starkbank.Transfer]:
        return iter(())

    def create(
        transfers: list[starkbank.Transfer],
        user: starkbank.Project | None = None,
    ) -> list[starkbank.Transfer]:
        captured.extend(transfers)
        transfers[0].id = "transfer-1"
        transfers[0].status = "created"
        return transfers

    monkeypatch.setattr(starkbank.transfer, "query", query)
    monkeypatch.setattr(starkbank.transfer, "create", create)
    command = build_transfer_command(InvoiceId("invoice-1"), Cents(9_950))

    # When
    result = sdk_client.ensure_transfer(command)

    # Then
    transfer = captured[0]
    assert result.id == "transfer-1"
    assert transfer.amount == 9_950
    assert transfer.bank_code == "20018183"
    assert transfer.branch_code == "0001"
    assert transfer.account_number == "6341320293482496"
    assert transfer.name == "Stark Bank S.A."
    assert transfer.tax_id == "20.018.183/0001-80"
    assert transfer.account_type == "payment"
    assert transfer.external_id == "trial-transfer-invoice-1"


def test_find_invoice_returns_tagged_invoice_or_none(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    # Given
    invoice = starkbank.Invoice(amount=1_000, tax_id="52998224725", name="Maria")
    invoice.id = "invoice-1"
    responses = iter((iter((invoice,)), iter(())))

    def query(
        limit: int | None = None,
        tags: list[str] | None = None,
        user: starkbank.Project | None = None,
    ) -> Iterator[starkbank.Invoice]:
        return next(responses)

    monkeypatch.setattr(starkbank.invoice, "query", query)

    # When
    found = sdk_client.find_invoice("trial-invoice:draft-1")
    missing = sdk_client.find_invoice("trial-invoice:missing")

    # Then
    assert found is not None
    assert found.id == InvoiceId("invoice-1")
    assert missing is None


def test_ensure_transfer_reuses_remote_transfer(
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

    def query(
        limit: int | None = None,
        tags: list[str] | None = None,
        user: starkbank.Project | None = None,
    ) -> Iterator[starkbank.Transfer]:
        return iter((transfer,))

    monkeypatch.setattr(starkbank.transfer, "query", query)

    # When
    result = sdk_client.ensure_transfer(command)

    # Then
    assert result.id == TransferId("transfer-existing")


def test_ensure_webhook_reuses_or_creates_invoice_subscription(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    # Given
    existing = starkbank.Webhook(
        "https://trial.example.com/webhooks/starkbank",
        ["invoice"],
        id="webhook-existing",
    )
    query_results = iter((iter((existing,)), iter(())))

    def query(
        limit: int | None = None,
        user: starkbank.Project | None = None,
    ) -> Iterator[starkbank.Webhook]:
        return next(query_results)

    def create(
        url: str,
        subscriptions: list[str],
        user: starkbank.Project | None = None,
    ) -> starkbank.Webhook:
        return starkbank.Webhook(url, subscriptions, id="webhook-created")

    monkeypatch.setattr(starkbank.webhook, "query", query)
    monkeypatch.setattr(starkbank.webhook, "create", create)

    # When
    reused = sdk_client.ensure_webhook(existing.url)
    created = sdk_client.ensure_webhook("https://new.example.com/webhooks/starkbank")

    # Then
    assert reused.id == "webhook-existing"
    assert created.id == "webhook-created"
    assert created.subscriptions == ("invoice",)


def test_list_webhooks_maps_safe_provider_fields(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    # Given
    webhook = starkbank.Webhook(
        "https://trial.example.com/webhooks/starkbank",
        ["invoice"],
        id="webhook-1",
    )

    def query(
        limit: int | None = None,
        user: starkbank.Project | None = None,
    ) -> Iterator[starkbank.Webhook]:
        return iter((webhook,))

    monkeypatch.setattr(starkbank.webhook, "query", query)

    # When
    result = sdk_client.list_webhooks()

    # Then
    assert result[0].id == "webhook-1"
    assert result[0].url == "https://trial.example.com/webhooks/starkbank"
    assert result[0].subscriptions == ("invoice",)
