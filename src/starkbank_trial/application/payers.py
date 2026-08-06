from datetime import datetime
from hashlib import sha256
from random import Random
from uuid import NAMESPACE_URL, uuid5

from faker import Faker

from starkbank_trial.domain.invoices import InvoiceDraft
from starkbank_trial.domain.status import DraftStatus
from starkbank_trial.domain.trials import InvoiceBatch
from starkbank_trial.domain.types import BatchId, Cents, DraftId


def build_invoice_drafts(batch: InvoiceBatch, created_at: datetime) -> tuple[InvoiceDraft, ...]:
    seed = int.from_bytes(sha256(str(batch.id).encode()).digest()[:8])
    random = Random(seed)
    fake = Faker("pt_BR", use_weighting=False)
    fake.seed_instance(seed)
    return tuple(
        InvoiceDraft(
            id=DraftId(str(uuid5(NAMESPACE_URL, f"{batch.id}:draft:{ordinal}"))),
            batch_id=batch.id,
            ordinal=ordinal,
            payer_name=str(fake.name()),
            payer_tax_id=str(fake.cpf()),
            amount=Cents(random.randint(1_000, 10_000)),
            tag=f"trial-draft:{uuid5(NAMESPACE_URL, f'{batch.id}:draft:{ordinal}')}",
            status=DraftStatus.PENDING,
            attempts=0,
            created_at=created_at,
        )
        for ordinal in range(batch.target_count)
    )


def build_smoke_invoice(reference: str, amount: int, created_at: datetime) -> InvoiceDraft:
    draft_id = uuid5(NAMESPACE_URL, f"starkbank-trial:smoke:{reference}")
    seed = int.from_bytes(sha256(str(draft_id).encode()).digest()[:8])
    fake = Faker("pt_BR", use_weighting=False)
    fake.seed_instance(seed)
    return InvoiceDraft(
        id=DraftId(str(draft_id)),
        batch_id=BatchId("sandbox-smoke"),
        ordinal=0,
        payer_name=str(fake.name()),
        payer_tax_id=str(fake.cpf()),
        amount=Cents(amount),
        tag=f"trial-smoke:{draft_id}",
        status=DraftStatus.PENDING,
        attempts=0,
        created_at=created_at,
    )
