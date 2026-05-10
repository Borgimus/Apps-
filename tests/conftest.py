"""
Shared pytest fixtures.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
from typing import List
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from app.brokers.broker_interface import (
    AccountInfo,
    OptionChain,
    OptionContract,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Quote,
)
from app.config import Settings

ET = ZoneInfo("America/New_York")


# ── Settings fixture ──────────────────────────────────────────────────────────

@pytest.fixture
def test_settings():
    """Settings with safe test defaults."""
    s = Settings(
        live_trading_enabled=False,
        broker="paper",
        database_url="sqlite+aiosqlite:///:memory:",
        kill_switch_file="./TEST_KILL_SWITCH",
    )
    return s


# ── OHLCV bar fixtures ────────────────────────────────────────────────────────

def _make_bars(n: int = 100, base_price: float = 450.0, interval: str = "1d") -> pd.DataFrame:
    np.random.seed(42)
    idx = pd.date_range("2024-01-02", periods=n, freq="B", tz="UTC")
    prices = base_price + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame(
        {
            "open": prices - np.abs(np.random.randn(n) * 0.3),
            "high": prices + np.abs(np.random.randn(n) * 0.5),
            "low": prices - np.abs(np.random.randn(n) * 0.5),
            "close": prices,
            "volume": (np.random.randint(50_000_000, 200_000_000, n)).astype(float),
        },
        index=idx,
    )
    return df


@pytest.fixture
def spy_daily_bars():
    return _make_bars(n=150, base_price=450.0)


@pytest.fixture
def spy_intraday_bars():
    """5-minute bars for a single trading day."""
    n = 78  # 9:30 → 4:00 ET in 5-min bars
    idx = pd.date_range(
        "2024-01-02 14:30", periods=n, freq="5min", tz="UTC"
    )
    np.random.seed(7)
    prices = 450.0 + np.cumsum(np.random.randn(n) * 0.1)
    df = pd.DataFrame(
        {
            "open": prices - 0.05,
            "high": prices + np.abs(np.random.randn(n) * 0.1),
            "low": prices - np.abs(np.random.randn(n) * 0.1),
            "close": prices,
            "volume": (np.random.randint(100_000, 5_000_000, n)).astype(float),
        },
        index=idx,
    )
    return df


# ── Broker fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def mock_account():
    return AccountInfo(
        account_id="test-123",
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        buying_power=Decimal("100000"),
        is_paper=True,
    )


@pytest.fixture
def mock_option_contract():
    return OptionContract(
        symbol="SPY",
        option_symbol="SPY240102C00450000",
        expiration=date(2024, 1, 2),
        strike=Decimal("450"),
        option_type="call",
        bid=Decimal("2.00"),
        ask=Decimal("2.10"),
        last=Decimal("2.05"),
        volume=500,
        open_interest=1000,
        implied_volatility=0.20,
        delta=0.42,
    )


@pytest.fixture
def mock_option_chain(mock_option_contract):
    return OptionChain(
        symbol="SPY",
        expiration=date(2024, 1, 2),
        underlying_price=Decimal("450.00"),
        calls=[mock_option_contract],
        puts=[
            OptionContract(
                symbol="SPY",
                option_symbol="SPY240102P00445000",
                expiration=date(2024, 1, 2),
                strike=Decimal("445"),
                option_type="put",
                bid=Decimal("1.80"),
                ask=Decimal("1.95"),
                last=Decimal("1.87"),
                volume=400,
                open_interest=800,
                implied_volatility=0.22,
                delta=-0.40,
            )
        ],
        fetched_at=datetime.utcnow(),
    )


@pytest.fixture
def mock_broker(mock_account, mock_option_chain):
    broker = AsyncMock()
    broker.get_account.return_value = mock_account
    broker.get_positions.return_value = []
    broker.get_option_chain.return_value = mock_option_chain
    broker.get_available_expirations.return_value = [date(2024, 1, 2)]
    broker.place_option_order.return_value = OrderResult(
        order_id="test-order-1",
        status=OrderStatus.FILLED,
        symbol="SPY",
        option_symbol="SPY240102C00450000",
        side=OrderSide.BUY_TO_OPEN,
        quantity=1,
        limit_price=Decimal("2.10"),
        filled_price=Decimal("2.10"),
        filled_quantity=1,
        submitted_at=datetime.utcnow(),
        filled_at=datetime.utcnow(),
    )
    return broker


# ── Datetime helpers ──────────────────────────────────────────────────────────

@pytest.fixture
def market_open_time():
    """9:45 AM ET — 15 min after open (safe trading window)."""
    return datetime(2024, 1, 2, 9, 45, tzinfo=ET)


@pytest.fixture
def pre_market_time():
    """9:35 AM ET — within the no-trade buffer after open."""
    return datetime(2024, 1, 2, 9, 35, tzinfo=ET)


@pytest.fixture
def near_close_time():
    """3:50 PM ET — within the no-trade buffer before close."""
    return datetime(2024, 1, 2, 15, 50, tzinfo=ET)
