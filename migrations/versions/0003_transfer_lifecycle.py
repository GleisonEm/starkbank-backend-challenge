from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_transfer_lifecycle"
down_revision: str | None = "0002_invoice_retry_schedule"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("webhook_events", sa.Column("transfer_id", sa.String(length=32)))
    op.add_column("transfer_jobs", sa.Column("provider_status", sa.String(length=32)))
    op.add_column("transfer_jobs", sa.Column("provider_log_type", sa.String(length=40)))
    op.add_column(
        "transfer_jobs",
        sa.Column("provider_status_updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_transfer_jobs_provider_status_created_at",
        "transfer_jobs",
        ["provider_status", "created_at", "id"],
    )
    op.create_index(
        "ix_webhook_events_received_at_id",
        "webhook_events",
        ["received_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_events_received_at_id", table_name="webhook_events")
    op.drop_index(
        "ix_transfer_jobs_provider_status_created_at",
        table_name="transfer_jobs",
    )
    op.drop_column("transfer_jobs", "provider_status_updated_at")
    op.drop_column("transfer_jobs", "provider_log_type")
    op.drop_column("transfer_jobs", "provider_status")
    op.drop_column("webhook_events", "transfer_id")
