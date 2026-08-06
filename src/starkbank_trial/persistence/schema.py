from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

metadata = MetaData(
    naming_convention={
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "pk": "pk_%(table_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
    }
)

trial_runs = Table(
    "trial_runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("status", String(24), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("ends_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    Column("active_marker", Integer, unique=True),
    CheckConstraint("active_marker IS NULL OR active_marker = 1", name="active_marker"),
)

invoice_batches = Table(
    "invoice_batches",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), ForeignKey("trial_runs.id", ondelete="CASCADE"), nullable=False),
    Column("slot_index", Integer, nullable=False),
    Column("scheduled_at", DateTime(timezone=True), nullable=False),
    Column("target_count", Integer, nullable=False),
    Column("status", String(24), nullable=False),
    Column("lease_until", DateTime(timezone=True)),
    Column("attempts", Integer, nullable=False, default=0),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    UniqueConstraint("run_id", "slot_index"),
    CheckConstraint("target_count BETWEEN 8 AND 12", name="target_count"),
    CheckConstraint("slot_index BETWEEN 0 AND 7", name="slot_index"),
)

invoice_drafts = Table(
    "invoice_drafts",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "batch_id",
        String(36),
        ForeignKey("invoice_batches.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("ordinal", Integer, nullable=False),
    Column("payer_name", String(160), nullable=False),
    Column("payer_tax_id", String(20), nullable=False),
    Column("amount", Integer, nullable=False),
    Column("tag", String(80), nullable=False, unique=True),
    Column("status", String(24), nullable=False),
    Column("provider_invoice_id", String(32), unique=True),
    Column("attempts", Integer, nullable=False, default=0),
    Column("reconcile_attempts", Integer, nullable=False, default=0),
    Column("last_error_code", String(80)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("batch_id", "ordinal"),
    CheckConstraint("amount > 0", name="amount"),
)
invoice_drafts.append_constraint(
    Index("ix_invoice_drafts_status_next_attempt_at", "status", "next_attempt_at")
)

webhook_events = Table(
    "webhook_events",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("subscription", String(40), nullable=False),
    Column("log_type", String(40), nullable=False),
    Column("invoice_id", String(32)),
    Column("workspace_id", String(32), nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
)

transfer_jobs = Table(
    "transfer_jobs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("event_id", String(64), ForeignKey("webhook_events.id"), nullable=False, unique=True),
    Column("invoice_id", String(32), nullable=False, unique=True),
    Column("amount", Integer, nullable=False),
    Column("fee", Integer, nullable=False),
    Column("net_amount", Integer, nullable=False),
    Column("external_id", String(80), nullable=False, unique=True),
    Column("tag", String(80), nullable=False, unique=True),
    Column("status", String(32), nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("lease_until", DateTime(timezone=True)),
    Column("provider_transfer_id", String(32), unique=True),
    Column("last_error_code", String(80)),
    Column("metadata", JSON),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("amount > 0", name="amount"),
    CheckConstraint("fee >= 0", name="fee"),
    CheckConstraint("net_amount > 0", name="net_amount"),
)
