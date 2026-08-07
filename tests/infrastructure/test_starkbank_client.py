from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import starkbank
from starkbank.error import InputErrors

from starkbank_trial.domain.events import CreditedInvoiceEvent, TransferLifecycleEvent
from starkbank_trial.domain.invoices import InvoiceDraft
from starkbank_trial.domain.provider import ProviderPermanentError, ProviderUnknownOutcomeError
from starkbank_trial.domain.status import DraftStatus
from starkbank_trial.domain.transfer import build_transfer_command
from starkbank_trial.domain.types import BatchId, Cents, DraftId, EventId, InvoiceId, TransferId
from starkbank_trial.infrastructure.starkbank_client import StarkBankClient


@pytest.fixture
def sdk_client() -> StarkBankClient:
    private_key, _ = starkbank.key.create()
    return StarkBankClient.from_credentials(
        "project-1",
        private_key,
        expected_workspace_id="workspace-1",
        live_operations_enabled=True,
    )


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


def test_verify_event_maps_transfer_lifecycle_from_signed_sdk_event(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    updated = datetime(2026, 8, 1, 12, 5, tzinfo=UTC)
    raw = SimpleNamespace(
        id="event-transfer-1",
        subscription="transfer",
        workspace_id="workspace-1",
        log=SimpleNamespace(
            type="success",
            transfer=SimpleNamespace(
                id="transfer-1",
                external_id="trial-transfer-invoice-1",
                status="success",
                updated=updated,
            ),
        ),
    )

    def parse(
        content: str,
        signature: str,
        user: starkbank.Project | None = None,
    ) -> object:
        return raw

    monkeypatch.setattr(starkbank.event, "parse", parse)

    result = sdk_client.verify_event(b"payload", "signature")

    assert isinstance(result, TransferLifecycleEvent)
    assert result.transfer_id == "transfer-1"
    assert result.external_id == "trial-transfer-invoice-1"
    assert result.status == "success"
    assert result.log_type == "success"
    assert result.updated_at == updated


def test_verify_event_normalizes_naive_transfer_updated_to_utc(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    # The SDK returns naive datetimes (starkcore check_datetime strips tzinfo);
    # the client must normalize to UTC so the event store can compare safely.
    updated = datetime(2026, 8, 1, 12, 5, tzinfo=UTC).replace(tzinfo=None)
    raw = SimpleNamespace(
        id="event-transfer-2",
        subscription="transfer",
        workspace_id="workspace-1",
        log=SimpleNamespace(
            type="success",
            transfer=SimpleNamespace(
                id="transfer-2",
                external_id="trial-transfer-invoice-2",
                status="success",
                updated=updated,
            ),
        ),
    )

    def parse(
        content: str,
        signature: str,
        user: starkbank.Project | None = None,
    ) -> object:
        return raw

    monkeypatch.setattr(starkbank.event, "parse", parse)

    result = sdk_client.verify_event(b"payload", "signature")

    assert isinstance(result, TransferLifecycleEvent)
    assert result.updated_at == datetime(2026, 8, 1, 12, 5, tzinfo=UTC)
    assert result.updated_at.tzinfo is not None


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


def test_ensure_webhook_reuses_or_creates_invoice_and_transfer_subscription(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    # Given
    existing = starkbank.Webhook(
        "https://trial.example.com/webhooks/starkbank",
        ["invoice", "transfer"],
        id="webhook-existing",
    )
    created = starkbank.Webhook(
        "https://new.example.com/webhooks/starkbank",
        ["invoice", "transfer"],
        id="webhook-created",
    )
    query_results = iter((iter((existing,)), iter(()), iter((created,))))

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
        return created

    monkeypatch.setattr(starkbank.webhook, "query", query)
    monkeypatch.setattr(starkbank.webhook, "create", create)

    # When
    reused = sdk_client.ensure_webhook(existing.url)
    created_result = sdk_client.ensure_webhook("https://new.example.com/webhooks/starkbank")

    # Then
    assert reused.id == "webhook-existing"
    assert created_result.id == "webhook-created"


def test_ensure_webhook_replaces_incomplete_subscription_before_creating(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    # Given: an invoice-only webhook exists for the URL (created before transfer support).
    stale = starkbank.Webhook(
        "https://trial.example.com/webhooks/starkbank",
        ["invoice"],
        id="webhook-stale",
    )
    created = starkbank.Webhook(
        "https://trial.example.com/webhooks/starkbank",
        ["invoice", "transfer"],
        id="webhook-created",
    )
    query_results = iter((iter((stale,)), iter((created,))))
    deleted: list[str] = []

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
        return created

    def delete(webhook_id: str, user: starkbank.Project | None = None) -> None:
        deleted.append(webhook_id)

    monkeypatch.setattr(starkbank.webhook, "query", query)
    monkeypatch.setattr(starkbank.webhook, "create", create)
    monkeypatch.setattr(starkbank.webhook, "delete", delete)

    # When
    result = sdk_client.ensure_webhook(stale.url)

    # Then: the incomplete webhook is replaced by a complete one and confirmed.
    assert deleted == ["webhook-stale"]
    assert result.id == "webhook-created"
    assert tuple(result.subscriptions) == ("invoice", "transfer")


def test_ensure_webhook_raises_unknown_when_created_webhook_not_confirmed(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    created = starkbank.Webhook(
        "https://trial.example.com/webhooks/starkbank",
        ["invoice", "transfer"],
        id="webhook-created",
    )

    def query(
        limit: int | None = None,
        user: starkbank.Project | None = None,
    ) -> Iterator[starkbank.Webhook]:
        return iter(())

    def create(
        url: str,
        subscriptions: list[str],
        user: starkbank.Project | None = None,
    ) -> starkbank.Webhook:
        return created

    monkeypatch.setattr(starkbank.webhook, "query", query)
    monkeypatch.setattr(starkbank.webhook, "create", create)

    with pytest.raises(ProviderUnknownOutcomeError) as excinfo:
        sdk_client.ensure_webhook(created.url)

    assert excinfo.value.operation == "create_webhook_unconfirmed"


def test_inspect_webhooks_classifies_active_and_stale(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    active = starkbank.Webhook(
        "https://trial.example.com/webhooks/starkbank",
        ["invoice", "transfer"],
        id="webhook-active",
    )
    stale = starkbank.Webhook(
        "https://trial.example.com/webhooks/starkbank",
        ["invoice"],
        id="webhook-stale",
    )
    other_url = starkbank.Webhook(
        "https://other.example.com/webhooks/starkbank",
        ["invoice"],
        id="webhook-other-url",
    )

    def query(
        limit: int | None = None,
        user: starkbank.Project | None = None,
    ) -> Iterator[starkbank.Webhook]:
        return iter((active, stale, other_url))

    monkeypatch.setattr(starkbank.webhook, "query", query)

    inspection = sdk_client.inspect_webhooks(active.url)

    assert inspection.active is not None
    assert inspection.active.id == "webhook-active"
    assert tuple(w.id for w in inspection.stale) == ("webhook-stale",)


def test_ensure_webhook_reuses_complete_transfer_subscription(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    existing = starkbank.Webhook(
        "https://trial.example.com/webhooks/starkbank",
        ["invoice", "transfer"],
        id="webhook-existing",
    )
    created = starkbank.Webhook(
        "https://trial.example.com/webhooks/starkbank",
        ["invoice", "transfer"],
        id="webhook-created",
    )

    def query(
        limit: int | None = None,
        user: starkbank.Project | None = None,
    ) -> Iterator[starkbank.Webhook]:
        return iter((existing,))

    def create(
        url: str,
        subscriptions: list[str],
        user: starkbank.Project | None = None,
    ) -> starkbank.Webhook:
        return created

    monkeypatch.setattr(starkbank.webhook, "query", query)
    monkeypatch.setattr(starkbank.webhook, "create", create)

    result = sdk_client.ensure_webhook(existing.url)

    assert result.id == "webhook-existing"


def test_delete_webhook_calls_sdk(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    deleted: list[str] = []

    def delete(webhook_id: str, user: starkbank.Project | None = None) -> None:
        deleted.append(webhook_id)

    monkeypatch.setattr(starkbank.webhook, "delete", delete)

    sdk_client.delete_webhook("webhook-1")

    assert deleted == ["webhook-1"]


def test_delete_webhook_requires_sdk_capability(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    monkeypatch.delattr(starkbank.webhook, "delete", raising=False)

    with pytest.raises(ProviderPermanentError):
        sdk_client.delete_webhook("webhook-1")


def test_ensure_webhook_input_error_is_permanent(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    def query(
        limit: int | None = None,
        user: starkbank.Project | None = None,
    ) -> Iterator[starkbank.Webhook]:
        return iter(())

    def create(
        url: str,
        subscriptions: list[str],
        user: starkbank.Project | None = None,
    ) -> starkbank.Webhook:
        raise InputErrors([{"code": "invalid", "message": "invalid webhook"}])

    monkeypatch.setattr(starkbank.webhook, "query", query)
    monkeypatch.setattr(starkbank.webhook, "create", create)

    with pytest.raises(ProviderPermanentError):
        sdk_client.ensure_webhook("https://trial.example.com/webhooks/starkbank")


def test_list_events_maps_safe_event_ids(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    # Given
    after = datetime(2026, 8, 7, 8, tzinfo=UTC)
    before = datetime(2026, 8, 7, 9, tzinfo=UTC)
    captured: dict[str, object] = {}

    def query(
        limit: int | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
        user: starkbank.Project | None = None,
    ) -> Iterator[object]:
        captured["after"] = after
        captured["before"] = before
        captured["user"] = user
        return iter(
            (
                SimpleNamespace(
                    id="event-1",
                    subscription="invoice",
                    workspace_id="workspace-1",
                    log=SimpleNamespace(type="credited"),
                ),
                SimpleNamespace(
                    id="event-2",
                    subscription="transfer",
                    workspace_id="workspace-1",
                    log=SimpleNamespace(type="created"),
                ),
            )
        )

    monkeypatch.setattr(starkbank.event, "query", query)

    # When
    result = sdk_client.list_events(after, before)

    # Then
    assert result == (EventId("event-1"), EventId("event-2"))
    # The SDK truncates datetime objects to dates, so the window must be sent
    # as UTC strings that keep hour precision.
    assert captured["after"] == "2026-08-07T08:00:00+00:00"
    assert captured["before"] == "2026-08-07T09:00:00+00:00"
    assert captured["user"] is sdk_client.user


def test_list_events_keeps_hour_precision_for_non_utc_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    # Given: a timezone-aware boundary that is not UTC (America/Sao_Paulo, UTC-3).
    sao_paulo = timezone(timedelta(hours=-3))
    captured: dict[str, object] = {}

    def query(
        limit: int | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
        user: starkbank.Project | None = None,
    ) -> Iterator[object]:
        captured["after"] = after
        return iter(())

    monkeypatch.setattr(starkbank.event, "query", query)

    # When
    result = sdk_client.list_events(
        datetime(2026, 8, 7, 5, tzinfo=sao_paulo),
        datetime(2026, 8, 7, 6, tzinfo=sao_paulo),
    )

    # Then: converted to UTC (08:00/09:00 UTC) and sent with hour precision.
    assert result == ()
    assert captured["after"] == "2026-08-07T08:00:00+00:00"


def test_list_events_without_upper_bound_passes_before_none(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    # Given
    captured: dict[str, object] = {}

    def query(
        limit: int | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
        user: starkbank.Project | None = None,
    ) -> Iterator[object]:
        captured["before"] = before
        return iter(())

    monkeypatch.setattr(starkbank.event, "query", query)

    # When
    result = sdk_client.list_events(datetime(2026, 8, 7, 8, tzinfo=UTC))

    # Then
    assert result == ()
    assert captured["before"] is None


def test_delete_event_calls_sdk(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: StarkBankClient,
) -> None:
    deleted: list[str] = []
    captured_user: list[starkbank.Project | None] = []

    def delete(
        event_id: str,
        user: starkbank.Project | None = None,
    ) -> None:
        deleted.append(event_id)
        captured_user.append(user)

    monkeypatch.setattr(starkbank.event, "delete", delete)

    sdk_client.delete_event(EventId("event-1"))

    assert deleted == ["event-1"]
    assert captured_user == [sdk_client.user]


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
