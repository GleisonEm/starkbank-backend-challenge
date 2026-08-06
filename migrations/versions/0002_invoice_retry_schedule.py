from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_invoice_retry_schedule"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invoice_drafts",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE invoice_drafts SET next_attempt_at = updated_at")
    with op.batch_alter_table("invoice_drafts") as batch_op:
        batch_op.alter_column("next_attempt_at", nullable=False)
    op.create_index(
        "ix_invoice_drafts_status_next_attempt_at",
        "invoice_drafts",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_invoice_drafts_status_next_attempt_at", table_name="invoice_drafts")
    op.drop_column("invoice_drafts", "next_attempt_at")
