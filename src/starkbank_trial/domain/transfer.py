from starkbank_trial.domain.constants import STARK_BANK_RECIPIENT
from starkbank_trial.domain.models import TransferCommand
from starkbank_trial.domain.types import Cents, ExternalId, InvoiceId


def build_transfer_command(invoice_id: InvoiceId, amount: Cents) -> TransferCommand:
    return TransferCommand(
        invoice_id=invoice_id,
        amount=amount,
        external_id=ExternalId(f"trial-transfer-{invoice_id}"),
        tag=f"trial-transfer:{invoice_id}",
        recipient=STARK_BANK_RECIPIENT,
    )
