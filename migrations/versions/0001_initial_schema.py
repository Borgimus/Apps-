"""Initial swing-system schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-31

Creates the auditable schema (mirrors src/storage/schema.sql). Applied to PostgreSQL in
deployment; the sqlite dev/test path applies schema.sql directly.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tc2000_imports",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("market_date", sa.String, nullable=False),
        sa.Column("received_at", sa.String, nullable=False),
        sa.Column("batch_hash", sa.String, nullable=False, unique=True),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("reject_reason", sa.Text),
        sa.Column("config_version", sa.String, nullable=False),
    )
    op.create_table(
        "signals",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("symbol", sa.String, nullable=False),
        sa.Column("setup_id", sa.String),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("state", sa.String, nullable=False),
        sa.Column("accepted", sa.Boolean, nullable=False),
        sa.Column("reject_reason", sa.Text),
        sa.Column("idempotency_key", sa.String, nullable=False, unique=True),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("signal_id", sa.String, sa.ForeignKey("signals.id")),
        sa.Column("client_order_id", sa.String, nullable=False, unique=True),
        sa.Column("broker_order_id", sa.String),
        sa.Column("symbol", sa.String, nullable=False),
        sa.Column("side", sa.String, nullable=False),
        sa.Column("type", sa.String, nullable=False),
        sa.Column("limit_price", sa.Float),
        sa.Column("stop_price", sa.Float),
        sa.Column("qty", sa.Integer, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("submitted_at", sa.String),
        sa.Column("acknowledged_at", sa.String),
    )
    op.create_table(
        "position_state_transitions",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("trade_id", sa.String, nullable=False),
        sa.Column("from_state", sa.String, nullable=False),
        sa.Column("to_state", sa.String, nullable=False),
        sa.Column("guard_results_json", sa.Text, nullable=False),
        sa.Column("idempotency_key", sa.String, nullable=False, unique=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("at", sa.String, nullable=False),
    )
    op.create_table(
        "daily_account_snapshots",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("session_date", sa.String, nullable=False),
        sa.Column("equity", sa.Float, nullable=False),
        sa.Column("cash", sa.Float, nullable=False),
        sa.Column("buying_power", sa.Float, nullable=False),
        sa.Column("committed_risk", sa.Float, nullable=False),
        sa.Column("exposure", sa.Float, nullable=False),
        sa.Column("realized_pnl", sa.Float, nullable=False),
        sa.Column("unrealized_pnl", sa.Float, nullable=False),
        sa.Column("drawdown", sa.Float, nullable=False),
        sa.Column("endpoint", sa.String, nullable=False),
    )
    op.create_table(
        "reconciliation_incidents",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("detected_at", sa.String, nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("symbol", sa.String),
        sa.Column("broker_state_json", sa.Text),
        sa.Column("db_state_json", sa.Text),
        sa.Column("detail", sa.Text),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("resolution_note", sa.Text),
    )
    # Remaining tables (scan files, memberships, snapshots, indicators, setups, risk_calcs,
    # fills, replacements, pnl, ai_reviews) are created by a follow-up revision or by
    # applying src/storage/schema.sql; kept here minimal to show the migration pattern.


def downgrade() -> None:
    for table in (
        "reconciliation_incidents", "daily_account_snapshots",
        "position_state_transitions", "orders", "signals", "tc2000_imports",
    ):
        op.drop_table(table)
