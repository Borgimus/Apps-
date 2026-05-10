"""Tests for RiskManager."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.brokers.broker_interface import OrderRequest, OrderSide, OrderType
from app.config import Settings
from app.risk import RiskCheck, RiskCheckResult, RiskManager

ET = ZoneInfo("America/New_York")


def _make_settings(**kwargs) -> Settings:
    defaults = dict(
        live_trading_enabled=False,
        broker="paper",
        database_url="sqlite+aiosqlite:///:memory:",
        kill_switch_file="./TEST_KILL_SWITCH_RISK",
        market_open="09:30",
        market_close="16:00",
        no_trade_open_buffer_minutes=15,
        no_trade_close_buffer_minutes=15,
    )
    defaults.update(kwargs)
    return Settings(**defaults)


def _make_order(**kwargs) -> OrderRequest:
    defaults = dict(
        symbol="SPY",
        option_symbol="SPY240102C00450000",
        side=OrderSide.BUY_TO_OPEN,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("2.10"),
    )
    defaults.update(kwargs)
    return OrderRequest(**defaults)


EQUITY = Decimal("100000")
SAFE_NOW = datetime(2024, 1, 2, 10, 0, tzinfo=ET)  # mid-session


# ── Kill switch ───────────────────────────────────────────────────────────────

def test_kill_switch_blocks_order(tmp_path, mock_option_contract):
    ks = tmp_path / "KILL"
    ks.touch()
    settings = _make_settings(kill_switch_file=str(ks))
    rm = RiskManager(settings)
    rm.start_session(EQUITY)

    result = rm.check_order(_make_order(), EQUITY, mock_option_contract, now=SAFE_NOW)
    assert not result.passed
    assert RiskCheck.KILL_SWITCH in result.failed_checks


def test_no_kill_switch_passes(mock_option_contract):
    settings = _make_settings(kill_switch_file="./DOES_NOT_EXIST_KS")
    rm = RiskManager(settings)
    rm.start_session(EQUITY)

    result = rm.check_order(_make_order(), EQUITY, mock_option_contract, now=SAFE_NOW)
    assert RiskCheck.KILL_SWITCH not in result.failed_checks


# ── Market order block ────────────────────────────────────────────────────────

def test_market_order_rejected(mock_option_contract):
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)

    order = _make_order(order_type=OrderType.MARKET)
    result = rm.check_order(order, EQUITY, mock_option_contract, now=SAFE_NOW)
    assert not result.passed
    assert RiskCheck.MARKET_ORDER in result.failed_checks


# ── Session buffer ────────────────────────────────────────────────────────────

def test_pre_open_buffer_blocks(pre_market_time, mock_option_contract):
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)
    result = rm.check_order(_make_order(), EQUITY, mock_option_contract, now=pre_market_time)
    assert not result.passed
    assert RiskCheck.SESSION_BUFFER in result.failed_checks


def test_near_close_buffer_blocks(near_close_time, mock_option_contract):
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)
    result = rm.check_order(_make_order(), EQUITY, mock_option_contract, now=near_close_time)
    assert not result.passed
    assert RiskCheck.SESSION_BUFFER in result.failed_checks


def test_mid_session_passes_buffer(market_open_time, mock_option_contract):
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)
    result = rm.check_order(_make_order(), EQUITY, mock_option_contract, now=market_open_time)
    assert RiskCheck.SESSION_BUFFER not in result.failed_checks


# ── Max trades per day ────────────────────────────────────────────────────────

def test_max_trades_per_day(mock_option_contract):
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)

    # Fill to the limit
    for _ in range(3):
        rm.record_trade()

    result = rm.check_order(_make_order(), EQUITY, mock_option_contract, now=SAFE_NOW)
    assert not result.passed
    assert RiskCheck.MAX_TRADES_PER_DAY in result.failed_checks


def test_under_max_trades_passes(mock_option_contract):
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)
    rm.record_trade()

    result = rm.check_order(_make_order(), EQUITY, mock_option_contract, now=SAFE_NOW)
    assert RiskCheck.MAX_TRADES_PER_DAY not in result.failed_checks


# ── Max daily loss ────────────────────────────────────────────────────────────

def test_max_daily_loss(mock_option_contract):
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)
    # Record a loss exceeding 2% of $100,000 = $2,000
    rm.record_trade(pnl=Decimal("-2500"))

    result = rm.check_order(_make_order(), EQUITY, mock_option_contract, now=SAFE_NOW)
    assert not result.passed
    assert RiskCheck.MAX_DAILY_LOSS in result.failed_checks


def test_within_daily_loss_passes(mock_option_contract):
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)
    rm.record_trade(pnl=Decimal("-500"))  # only 0.5% loss

    result = rm.check_order(_make_order(), EQUITY, mock_option_contract, now=SAFE_NOW)
    assert RiskCheck.MAX_DAILY_LOSS not in result.failed_checks


# ── Spread filter ─────────────────────────────────────────────────────────────

def test_wide_spread_rejected(mock_option_contract):
    from app.brokers.broker_interface import OptionContract

    wide_contract = OptionContract(
        symbol="SPY",
        option_symbol="SPY240102C00450000",
        expiration=mock_option_contract.expiration,
        strike=mock_option_contract.strike,
        option_type="call",
        bid=Decimal("1.00"),
        ask=Decimal("3.00"),  # 200% spread — clearly too wide
        last=Decimal("2.00"),
        volume=500,
        open_interest=500,
        implied_volatility=0.20,
    )
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)
    result = rm.check_order(_make_order(), EQUITY, wide_contract, now=SAFE_NOW)
    assert not result.passed
    assert RiskCheck.MAX_SPREAD_PCT in result.failed_checks


# ── Open interest / volume filters ───────────────────────────────────────────

def test_low_open_interest_rejected(mock_option_contract):
    from app.brokers.broker_interface import OptionContract

    low_oi = OptionContract(
        symbol="SPY",
        option_symbol="SPY240102C00450000",
        expiration=mock_option_contract.expiration,
        strike=mock_option_contract.strike,
        option_type="call",
        bid=Decimal("2.00"),
        ask=Decimal("2.10"),
        last=Decimal("2.05"),
        volume=500,
        open_interest=5,   # below min of 100
        implied_volatility=0.20,
    )
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)
    result = rm.check_order(_make_order(), EQUITY, low_oi, now=SAFE_NOW)
    assert not result.passed
    assert RiskCheck.MIN_OPEN_INTEREST in result.failed_checks


def test_low_volume_rejected(mock_option_contract):
    from app.brokers.broker_interface import OptionContract

    low_vol = OptionContract(
        symbol="SPY",
        option_symbol="SPY240102C00450000",
        expiration=mock_option_contract.expiration,
        strike=mock_option_contract.strike,
        option_type="call",
        bid=Decimal("2.00"),
        ask=Decimal("2.10"),
        last=Decimal("2.05"),
        volume=3,   # below min of 50
        open_interest=500,
        implied_volatility=0.20,
    )
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)
    result = rm.check_order(_make_order(), EQUITY, low_vol, now=SAFE_NOW)
    assert not result.passed
    assert RiskCheck.MIN_VOLUME in result.failed_checks


# ── Risk sizing ───────────────────────────────────────────────────────────────

def test_position_sizing():
    rm = RiskManager(_make_settings())
    # With $100k equity, 1% risk = $1,000 max
    # At $2.10 ask → cost per contract = $210
    # Should get floor($1000 / $210) = 4 contracts
    n = rm.position_size_contracts(EQUITY, Decimal("2.10"))
    assert n == 4


def test_position_sizing_expensive_option():
    rm = RiskManager(_make_settings())
    # At $15.00 ask → cost per contract = $1500 > $1000 → 0 contracts
    n = rm.position_size_contracts(EQUITY, Decimal("15.00"))
    assert n == 0


# ── Earnings blackout ─────────────────────────────────────────────────────────

def test_earnings_blackout_rejects(mock_option_contract):
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)
    earnings_cal = {"SPY": [date(2024, 1, 2)]}  # earnings today
    result = rm.check_order(
        _make_order(), EQUITY, mock_option_contract, earnings_calendar=earnings_cal, now=SAFE_NOW
    )
    assert not result.passed
    assert RiskCheck.EARNINGS_BLACKOUT in result.failed_checks


def test_earnings_outside_blackout_passes(mock_option_contract):
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)
    earnings_cal = {"SPY": [date(2024, 2, 15)]}  # far future
    result = rm.check_order(
        _make_order(), EQUITY, mock_option_contract, earnings_calendar=earnings_cal, now=SAFE_NOW
    )
    assert RiskCheck.EARNINGS_BLACKOUT not in result.failed_checks
