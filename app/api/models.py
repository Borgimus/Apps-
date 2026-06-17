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


# ── ICT-specific ORM models ───────────────────────────────────────────────────

class ICTTrade(Base):
    """One simulated or live ICT trade."""
    __tablename__ = "ict_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(16), nullable=False, index=True)
    direction = Column(String(8), nullable=False)          # "long" | "short"
    entry_time = Column(DateTime, nullable=False, index=True)
    exit_time = Column(DateTime)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float)
    stop_loss = Column(Float, nullable=False)
    take_profit = Column(Float, nullable=False)
    position_size = Column(Float, nullable=False)
    risk_amount = Column(Float, nullable=False)
    pnl = Column(Float)
    rr_achieved = Column(Float)
    trade_duration_minutes = Column(Integer)
    exit_reason = Column(String(16))                       # "sl" | "tp" | "eod"
    fvg_type = Column(String(16))                          # "BULLISH" | "BEARISH"
    sweep_type = Column(String(32))                        # e.g. "ASIAN_HIGH"
    backtest_result_id = Column(Integer, index=True)       # FK ref (no constraint for simplicity)
    created_at = Column(DateTime, server_default=func.now())


class ICTBacktestResult(Base):
    """Aggregate metrics for one ICT backtest run."""
    __tablename__ = "ict_backtest_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), unique=True, nullable=False, index=True)
    symbol = Column(String(16), nullable=False, index=True)
    start_date = Column(String(16))
    end_date = Column(String(16))
    status = Column(String(16), default="pending", index=True)  # pending/running/complete/error
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades = Column(Integer, default=0)
    win_rate = Column(Float)
    long_win_rate = Column(Float)
    short_win_rate = Column(Float)
    avg_rr = Column(Float)
    profit_factor = Column(Float)
    expectancy = Column(Float)
    total_pnl = Column(Float)
    total_return = Column(Float)
    monthly_return = Column(Float)
    max_drawdown = Column(Float)
    sharpe_ratio = Column(Float)
    strategy_params_json = Column(Text)   # JSON blob of ICTConfig overrides
    error_message = Column(Text)
    ran_at = Column(DateTime, server_default=func.now())


class ICTConfig(Base):
    """Persisted ICT strategy configuration (one row = one named config)."""
    __tablename__ = "ict_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), unique=True, nullable=False, default="default")
    tick_size = Column(Float, default=0.25)
    min_sweep_ticks = Column(Float, default=2.0)
    rejection_candles = Column(Integer, default=3)
    rejection_close_pct = Column(Float, default=0.5)
    min_fvg_size = Column(Float, default=0.0)
    fvg_fill_pct_entry = Column(Float, default=0.0)
    fvg_lookback_bars = Column(Integer, default=20)
    swing_lookback = Column(Integer, default=5)
    equal_threshold_ticks = Column(Float, default=2.0)
    sl_buffer_ticks = Column(Float, default=2.0)
    exit_mode = Column(String(32), default="fixed_rr")
    fixed_rr_ratio = Column(Float, default=2.0)
    account_size = Column(Float, default=100_000.0)
    risk_per_trade_pct = Column(Float, default=0.01)
    max_daily_loss_pct = Column(Float, default=0.02)
    max_trades_per_day = Column(Integer, default=3)
    trading_start_hour = Column(Integer, default=14)
    stop_trading_hour = Column(Integer, default=21)
    min_confidence = Column(Float, default=0.6)
    asian_start_hour = Column(Integer, default=0)
    asian_end_hour = Column(Integer, default=6)
    london_start_hour = Column(Integer, default=3)
    london_end_hour = Column(Integer, default=8)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now(), server_default=func.now())
