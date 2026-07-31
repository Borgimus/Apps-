"""SQLAlchemy 2.0 ORM models for PostgreSQL deployment.

These mirror src/storage/schema.sql, which remains the canonical schema of record used by the
dependency-light sqlite path (dev/tests). Importing this module requires SQLAlchemy; the
deterministic test-suite skips it when the dependency is absent (pytest.importorskip).

Apply in deployment via Alembic (migrations/). Postgres upgrades TEXT JSON columns to jsonb.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TC2000Import(Base):
    __tablename__ = "tc2000_imports"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    market_date: Mapped[str] = mapped_column(String, nullable=False)
    received_at: Mapped[str] = mapped_column(String, nullable=False)
    batch_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    config_version: Mapped[str] = mapped_column(String, nullable=False)


class TC2000ScanFile(Base):
    __tablename__ = "tc2000_scan_files"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    import_id: Mapped[str] = mapped_column(ForeignKey("tc2000_imports.id"), nullable=False)
    scan: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    file_hash: Mapped[str] = mapped_column(String, nullable=False)
    raw_path: Mapped[str | None] = mapped_column(String)
    symbol_count: Mapped[int] = mapped_column(Integer, nullable=False)


class CandidateMembership(Base):
    __tablename__ = "candidate_membership"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    import_id: Mapped[str] = mapped_column(ForeignKey("tc2000_imports.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    in_1m: Mapped[bool] = mapped_column(Boolean, nullable=False)
    in_3m: Mapped[bool] = mapped_column(Boolean, nullable=False)
    in_6m: Mapped[bool] = mapped_column(Boolean, nullable=False)
    agreement_count: Mapped[int] = mapped_column(Integer, nullable=False)
    mode_3of3: Mapped[bool] = mapped_column(Boolean, nullable=False)
    mode_2of3: Mapped[bool] = mapped_column(Boolean, nullable=False)
    mode_union: Mapped[bool] = mapped_column(Boolean, nullable=False)
    composite_strength: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String, default="TC2000", nullable=False)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    signal_id: Mapped[str | None] = mapped_column(ForeignKey("signals.id"))
    client_order_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    broker_order_id: Mapped[str | None] = mapped_column(String)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    limit_price: Mapped[float | None] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    submitted_at: Mapped[str | None] = mapped_column(String)
    acknowledged_at: Mapped[str | None] = mapped_column(String)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    setup_id: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True, nullable=False)


class PositionStateTransition(Base):
    __tablename__ = "position_state_transitions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    trade_id: Mapped[str] = mapped_column(String, nullable=False)
    from_state: Mapped[str] = mapped_column(String, nullable=False)
    to_state: Mapped[str] = mapped_column(String, nullable=False)
    guard_results_json: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[str] = mapped_column(String, nullable=False)


class DailyAccountSnapshot(Base):
    __tablename__ = "daily_account_snapshots"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_date: Mapped[str] = mapped_column(String, nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    cash: Mapped[float] = mapped_column(Float, nullable=False)
    buying_power: Mapped[float] = mapped_column(Float, nullable=False)
    committed_risk: Mapped[float] = mapped_column(Float, nullable=False)
    exposure: Mapped[float] = mapped_column(Float, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    drawdown: Mapped[float] = mapped_column(Float, nullable=False)
    endpoint: Mapped[str] = mapped_column(String, nullable=False)


class ReconciliationIncident(Base):
    __tablename__ = "reconciliation_incidents"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    detected_at: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    symbol: Mapped[str | None] = mapped_column(String)
    broker_state_json: Mapped[str | None] = mapped_column(Text)
    db_state_json: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolution_note: Mapped[str | None] = mapped_column(Text)
