from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_owned_invoices"
down_revision: str | None = "0003_transfer_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "owned_invoices",
        sa.Column("tag", sa.String(length=80), nullable=False),
        sa.Column("provider_invoice_id", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("draft_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source IN ('trial', 'smoke')",
            name="ck_owned_invoices_source",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["invoice_drafts.id"],
            name="fk_owned_invoices_draft_id_invoice_drafts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tag", name="pk_owned_invoices"),
        sa.UniqueConstraint(
            "draft_id",
            name="uq_owned_invoices_draft_id",
        ),
        sa.UniqueConstraint(
            "provider_invoice_id",
            name="uq_owned_invoices_provider_invoice_id",
        ),
    )
    op.create_index(
        "ix_owned_invoices_provider_invoice_id",
        "owned_invoices",
        ["provider_invoice_id"],
    )
    op.execute(
        sa.text(
            "INSERT INTO owned_invoices "
            "(tag, provider_invoice_id, source, draft_id, created_at, updated_at) "
            "SELECT d.tag, d.provider_invoice_id, "
            "CASE WHEN d.batch_id = 'sandbox-smoke' THEN 'smoke' ELSE 'trial' END, "
            "d.id, d.created_at, d.updated_at FROM invoice_drafts AS d"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_owned_invoices_provider_invoice_id", table_name="owned_invoices")
    op.drop_table("owned_invoices")
