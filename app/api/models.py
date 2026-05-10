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
