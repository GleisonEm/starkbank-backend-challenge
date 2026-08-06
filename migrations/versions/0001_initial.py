from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trial_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_marker", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "active_marker IS NULL OR active_marker = 1",
            name="ck_trial_runs_active_marker",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_trial_runs"),
        sa.UniqueConstraint("active_marker", name="uq_trial_runs_active_marker"),
    )
    op.create_table(
        "invoice_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("slot_index", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "slot_index BETWEEN 0 AND 7",
            name="ck_invoice_batches_slot_index",
        ),
        sa.CheckConstraint(
            "target_count BETWEEN 8 AND 12",
            name="ck_invoice_batches_target_count",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["trial_runs.id"],
            name="fk_invoice_batches_run_id_trial_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_batches"),
        sa.UniqueConstraint("run_id", "slot_index", name="uq_invoice_batches_run_id"),
    )
    op.create_table(
        "invoice_drafts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("payer_name", sa.String(length=160), nullable=False),
        sa.Column("payer_tax_id", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("tag", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("provider_invoice_id", sa.String(length=32), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("reconcile_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_invoice_drafts_amount"),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["invoice_batches.id"],
            name="fk_invoice_drafts_batch_id_invoice_batches",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_drafts"),
        sa.UniqueConstraint("batch_id", "ordinal", name="uq_invoice_drafts_batch_id"),
        sa.UniqueConstraint("provider_invoice_id", name="uq_invoice_drafts_provider_invoice_id"),
        sa.UniqueConstraint("tag", name="uq_invoice_drafts_tag"),
    )
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("subscription", sa.String(length=40), nullable=False),
        sa.Column("log_type", sa.String(length=40), nullable=False),
        sa.Column("invoice_id", sa.String(length=32), nullable=True),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_webhook_events"),
    )
    op.create_table(
        "transfer_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("invoice_id", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("fee", sa.Integer(), nullable=False),
        sa.Column("net_amount", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=80), nullable=False),
        sa.Column("tag", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_transfer_id", sa.String(length=32), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_transfer_jobs_amount"),
        sa.CheckConstraint("fee >= 0", name="ck_transfer_jobs_fee"),
        sa.CheckConstraint("net_amount > 0", name="ck_transfer_jobs_net_amount"),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["webhook_events.id"],
            name="fk_transfer_jobs_event_id_webhook_events",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_transfer_jobs"),
        sa.UniqueConstraint("event_id", name="uq_transfer_jobs_event_id"),
        sa.UniqueConstraint("external_id", name="uq_transfer_jobs_external_id"),
        sa.UniqueConstraint("invoice_id", name="uq_transfer_jobs_invoice_id"),
        sa.UniqueConstraint(
            "provider_transfer_id",
            name="uq_transfer_jobs_provider_transfer_id",
        ),
        sa.UniqueConstraint("tag", name="uq_transfer_jobs_tag"),
    )


def downgrade() -> None:
    op.drop_table("transfer_jobs")
    op.drop_table("webhook_events")
    op.drop_table("invoice_drafts")
    op.drop_table("invoice_batches")
    op.drop_table("trial_runs")
