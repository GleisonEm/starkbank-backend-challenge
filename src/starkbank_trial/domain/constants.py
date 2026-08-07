from typing import Final
from uuid import NAMESPACE_URL, uuid5

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

# Stable identifiers for the local smoke invoices. They are persisted in the
# local database so webhook credits from this environment are recognized, and
# ignored by any other environment running in the same Sandbox workspace.
SMOKE_RUN_ID: Final[str] = str(uuid5(NAMESPACE_URL, "starkbank-trial:smoke-run"))
SMOKE_BATCH_ID: Final[str] = "sandbox-smoke"
