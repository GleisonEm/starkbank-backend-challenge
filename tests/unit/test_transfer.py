from starkbank_trial.domain.constants import STARK_BANK_RECIPIENT
from starkbank_trial.domain.transfer import build_transfer_command
from starkbank_trial.domain.types import Cents, InvoiceId


def test_build_transfer_command_uses_exact_challenge_recipient() -> None:
    # Given
    invoice_id = InvoiceId("5656565656565656")

    # When
    command = build_transfer_command(invoice_id, Cents(9_950))

    # Then
    assert command.recipient == STARK_BANK_RECIPIENT
    assert command.amount == Cents(9_950)
    assert command.external_id == "trial-transfer-5656565656565656"
    assert STARK_BANK_RECIPIENT.bank_code == "20018183"
    assert STARK_BANK_RECIPIENT.branch_code == "0001"
    assert STARK_BANK_RECIPIENT.account_number == "6341320293482496"
    assert STARK_BANK_RECIPIENT.name == "Stark Bank S.A."
    assert STARK_BANK_RECIPIENT.tax_id == "20.018.183/0001-80"
    assert STARK_BANK_RECIPIENT.account_type == "payment"
