from typing import Final

from starkbank_trial.domain.models import Recipient

STARK_BANK_RECIPIENT: Final = Recipient(
    bank_code="20018183",
    branch_code="0001",
    account_number="6341320293482496",
    name="Stark Bank S.A.",
    tax_id="20.018.183/0001-80",
    account_type="payment",
)

WEBHOOK_SUBSCRIPTIONS: Final[frozenset[str]] = frozenset({"invoice", "transfer"})
