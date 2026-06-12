"""
SQLAlchemy ORM models and async database setup.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from ..config import get_settings

_settings = get_settings()
_engine = create_async_engine(_settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(_engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_schema(conn)


async def _migrate_schema(conn) -> None:
    """Add new columns to existing tables without dropping data (idempotent)."""
    from sqlalchemy import text
    migrations = [
        ("trade_journal", "filled_at",         "DATETIME"),
        ("trade_journal", "time_to_fill_secs", "FLOAT"),
        ("trade_journal", "exit_bid",           "FLOAT"),
        ("trade_journal", "exit_ask",           "FLOAT"),
        ("trade_journal", "exit_spread_pct",    "FLOAT"),
        ("trade_journal", "limit_price_mode",   "VARCHAR(32)"),
        ("trade_journal", "peak_price",         "FLOAT"),
        ("trade_journal", "trough_price",       "FLOAT"),
        ("trade_journal", "mfe",                "FLOAT"),
        ("trade_journal", "mae",                "FLOAT"),
        ("scan_results",  "universe_group",     "VARCHAR(32)"),
        ("signal_bridge", "confluence_count",             "INTEGER"),
        ("signal_bridge", "reconciliation_passed",         "BOOLEAN"),
        ("signal_bridge", "underlying_price_at_signal",    "FLOAT"),
        ("signal_bridge", "orb_slot_reserved",             "BOOLEAN"),
        ("signal_bridge", "orb_fwd_price_5m",              "FLOAT"),
        ("signal_bridge", "orb_fwd_price_15m",             "FLOAT"),
        ("signal_bridge", "orb_fwd_price_30m",             "FLOAT"),
        ("signal_bridge", "orb_fwd_pct_5m",                "FLOAT"),
        ("signal_bridge", "orb_fwd_pct_15m",               "FLOAT"),
        ("signal_bridge", "orb_fwd_pct_30m",               "FLOAT"),
    ]
    for table, col, col_type in migrations:
        try:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
        except Exception:
            pass  # column already exists — safe to ignore


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# ── ORM Models ────────────────────────────────────────────────────────────────

class DBSignal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(String(64), nullable=False, index=True)
    symbol = Column(String(16), nullable=False, index=True)
    direction = Column(String(16), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    price = Column(Float, nullable=False)
    confidence = Column(Float)
    notes = Column(Text)
    metadata_json = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class DBOrder(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(128), unique=True, nullable=False, index=True)
    strategy_id = Column(String(64), index=True)
    symbol = Column(String(16), nullable=False, index=True)
    option_symbol = Column(String(64), nullable=False)
    side = Column(String(32), nullable=False)
    quantity = Column(Integer, nullable=False)
    limit_price = Column(Float, nullable=False)
    filled_price = Column(Float)
    filled_quantity = Column(Integer)
    status = Column(String(32), nullable=False, index=True)
    is_paper = Column(Boolean, default=True)
    submitted_at = Column(DateTime)
    filled_at = Column(DateTime)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class DBPosition(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(16), nullable=False, index=True)
    option_symbol = Column(String(64), nullable=False, unique=True, index=True)
    quantity = Column(Integer, nullable=False)
    avg_cost = Column(Float, nullable=False)
    current_price = Column(Float)
    unrealized_pnl = Column(Float)
    strategy_id = Column(String(64))
    opened_at = Column(DateTime)
    updated_at = Column(DateTime, onupdate=func.now(), server_default=func.now())


class DBBacktestResult(Base):
    __tablename__ = "backtest_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(String(64), nullable=False, index=True)
    symbol = Column(String(16), nullable=False, index=True)
    start_date = Column(String(16))
    end_date = Column(String(16))
    total_trades = Column(Integer)
    win_rate = Column(Float)
    profit_factor = Column(Float)
    total_pnl = Column(Float)
    max_drawdown = Column(Float)
    sharpe_ratio = Column(Float)
    expectancy = Column(Float)
    is_approximate = Column(Boolean, default=True)
    ran_at = Column(DateTime, server_default=func.now())
    report_path = Column(String(256))


class DBRiskEvent(Base):
    __tablename__ = "risk_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(64), nullable=False, index=True)
    symbol = Column(String(16))
    option_symbol = Column(String(64))
    check_name = Column(String(64))
    message = Column(Text)
    severity = Column(String(16), default="info")
    timestamp = Column(DateTime, server_default=func.now())


class DBTradeJournal(Base):
    """Full lifecycle of a single trade attempt — entry, exit, or rejection."""
    __tablename__ = "trade_journal"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Entry / signal
    entry_time = Column(DateTime, index=True)
    session_date = Column(String(10), index=True)   # YYYY-MM-DD for fast daily queries
    strategy_id = Column(String(64), nullable=False, index=True)
    signal_direction = Column(String(16))            # LONG / SHORT
    underlying_symbol = Column(String(16), nullable=False, index=True)
    underlying_price = Column(Float)
    weekday = Column(Integer)                        # 0=Mon … 4=Fri

    # Contract
    option_symbol = Column(String(64), index=True)
    expiration = Column(String(16))
    strike = Column(Float)
    option_type = Column(String(8))                  # call / put
    delta = Column(Float)
    iv = Column(Float)
    bid = Column(Float)
    ask = Column(Float)
    spread_pct = Column(Float)

    # Order
    limit_price = Column(Float)
    limit_price_mode = Column(String(32))             # bid / mid / ask / marketable_limit
    fill_price = Column(Float)
    quantity = Column(Integer)
    filled_quantity = Column(Integer)                # for partial fills
    order_id = Column(String(128), index=True)       # broker order id

    # Fill timing
    filled_at = Column(DateTime)                     # broker-confirmed fill timestamp
    time_to_fill_secs = Column(Float)                # seconds from entry_time to filled_at

    # Exit
    exit_time = Column(DateTime)
    exit_price = Column(Float)
    exit_reason = Column(String(32))                 # stop_loss / take_profit / trailing_stop / max_hold / eod_exit / cancellation / manual
    realized_pnl = Column(Float)
    unrealized_pnl = Column(Float)                   # last known if exiting early
    hold_duration_secs = Column(Float)
    slippage = Column(Float)                         # fill_price - limit_price
    exit_bid = Column(Float)                         # bid at time of exit
    exit_ask = Column(Float)                         # ask at time of exit
    exit_spread_pct = Column(Float)                  # (ask-bid)/mid at exit

    # Rejection
    rejection_reason = Column(Text)

    # MFE / MAE (maximum favourable / adverse excursion, in dollars, ×100 per contract)
    peak_price = Column(Float)                        # highest option price seen while open
    trough_price = Column(Float)                      # lowest option price seen while open
    mfe = Column(Float)                               # (peak_price - entry_price) × 100 × qty
    mae = Column(Float)                               # (trough_price - entry_price) × 100 × qty

    # Market context tags (optional JSON blob)
    regime_tags = Column(Text)                       # e.g. '{"vix":"high","trend":"up"}'

    # Metadata
    status = Column(String(16), nullable=False, default="open", index=True)  # open / closed / rejected / cancelled
    is_paper = Column(Boolean, default=True)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class DBPendingOrder(Base):
    """
    Persisted record of every pending broker order so the session can recover
    after a crash or restart without re-submitting already-placed orders.

    Updated in place by PendingOrderStore as the order progresses through
    the fill lifecycle (pending → partially_filled → filled / cancelled /
    rejected / expired).
    """
    __tablename__ = "pending_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(128), unique=True, nullable=False, index=True)
    journal_id = Column(Integer)                    # FK to trade_journal.id
    option_symbol = Column(String(64), nullable=False, index=True)
    symbol = Column(String(16), nullable=False, index=True)
    strategy_id = Column(String(64))
    direction = Column(String(16))                  # LONG / SHORT
    quantity = Column(Integer, nullable=False)
    limit_price = Column(Float, nullable=False)
    submitted_at = Column(DateTime, nullable=False)
    status = Column(String(32), nullable=False, default="pending", index=True)
    filled_quantity = Column(Integer, default=0)
    avg_fill_price = Column(Float)
    last_polled_at = Column(DateTime)
    session_date = Column(String(10), index=True)   # YYYY-MM-DD
    created_at = Column(DateTime, server_default=func.now())


class DBSessionLog(Base):
    """Structured log entries for each trading session poll cycle."""
    __tablename__ = "session_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_date = Column(String(10), index=True)
    timestamp = Column(DateTime, server_default=func.now(), index=True)
    level = Column(String(16), default="info")       # info / warning / error
    event = Column(String(64))                        # heartbeat / signal / order / exit / error
    symbol = Column(String(16))
    message = Column(Text)
    data_json = Column(Text)                          # arbitrary structured payload


class DBScanResult(Base):
    """
    Persisted record of each symbol evaluated by the scanning pipeline.

    Written after each universe scan; used for daily reports and dashboard.
    """
    __tablename__ = "scan_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_date = Column(String(10), index=True)           # YYYY-MM-DD
    symbol = Column(String(16), nullable=False, index=True)
    score = Column(Float)
    signal_type = Column(String(16))                        # LONG / SHORT / NEUTRAL
    reason_codes = Column(Text)                             # JSON list
    rejected_reasons = Column(Text)                         # JSON list
    is_rejected = Column(Boolean, default=False)
    selected = Column(Boolean, default=False)               # was this chosen for trading
    # Key metrics snapshot
    atr_pct = Column(Float)
    rvol = Column(Float)
    rsi = Column(Float)
    vwap = Column(Float)
    price = Column(Float)
    price_vs_vwap = Column(String(8))
    gap_pct = Column(Float)
    trend = Column(String(16))
    ma_compression = Column(Boolean)
    has_earnings = Column(Boolean)
    universe_group = Column(String(32))                  # e.g. "core_etfs", "mega_cap"
    scanned_at = Column(DateTime, server_default=func.now(), index=True)
    created_at = Column(DateTime, server_default=func.now())


class DBSignalBridge(Base):
    """
    Signal-to-trade bridge diagnostic.

    Written for every signal evaluated when PAPER_EVAL_PERMISSIVE_ENTRY_MODE is
    enabled.  Records actual gate values vs thresholds so every trade and every
    blocked signal is fully explained.
    """
    __tablename__ = "signal_bridge"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_date = Column(String(10), index=True)
    timestamp = Column(DateTime, nullable=False, index=True)

    # Signal identity
    symbol = Column(String(16), nullable=False, index=True)
    universe_group = Column(String(32))
    strategy_id = Column(String(64), nullable=False, index=True)
    signal_direction = Column(String(16))
    signal_age_seconds = Column(Float)

    # Scanner context (from universe scan result for today)
    scanner_score = Column(Float)
    scanner_approved = Column(Boolean)

    # Signal quality
    signal_quality_score = Column(Float)
    confluence_count = Column(Integer, default=1)

    # Option contract (populated once liquidity filter selects a contract)
    option_contract = Column(String(64))
    bid = Column(Float)
    ask = Column(Float)
    spread_pct = Column(Float)
    spread_threshold = Column(Float)

    # Liquidity metrics — actual value vs threshold (not just pass/fail)
    rvol = Column(Float)
    rvol_threshold = Column(Float)
    option_volume = Column(Integer)
    option_volume_threshold = Column(Integer)
    open_interest = Column(Integer)
    open_interest_threshold = Column(Integer)

    # Gate results
    liquidity_passed = Column(Boolean)
    spread_passed = Column(Boolean)
    risk_passed = Column(Boolean)
    reconciliation_passed = Column(Boolean, default=True)
    position_limit_passed = Column(Boolean, default=True)

    # Underlying price at signal time (for ORB forward performance)
    underlying_price_at_signal = Column(Float)

    # ORB slot reservation flag (True when this signal was evaluated while ORB reserve was active)
    orb_slot_reserved = Column(Boolean, default=False)

    # ORB forward performance (filled post-session via compute_orb_forward_performance)
    orb_fwd_price_5m = Column(Float)
    orb_fwd_price_15m = Column(Float)
    orb_fwd_price_30m = Column(Float)
    orb_fwd_pct_5m = Column(Float)
    orb_fwd_pct_15m = Column(Float)
    orb_fwd_pct_30m = Column(Float)

    # Final decision
    final_decision = Column(String(16))   # traded | blocked | skipped
    exact_block_reason = Column(Text)

    created_at = Column(DateTime, server_default=func.now())


class DBOrderStatusTransition(Base):
    """
    Telemetry: every broker order status change for a tracked order.

    Written by FillTracker.poll() each time the broker returns a new status.
    Enables post-session analysis of fill latency, status progression, and
    cancellation patterns.
    """
    __tablename__ = "order_status_transitions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(128), nullable=False, index=True)
    journal_id = Column(Integer)                     # FK to trade_journal.id
    option_symbol = Column(String(64), index=True)
    symbol = Column(String(16))
    prev_status = Column(String(32))                 # status before this transition
    status = Column(String(32), nullable=False)      # new status
    filled_qty = Column(Integer, default=0)
    avg_fill_price = Column(Float)
    bid = Column(Float)                              # bid snapshot at transition (if available)
    ask = Column(Float)                              # ask snapshot at transition (if available)
    spread_pct = Column(Float)
    timestamp = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now())
