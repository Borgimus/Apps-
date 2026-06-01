"""
Tests for the corrected max_trades_per_day semantics:
  - 'max_trades_per_day' is now max new ENTRIES per day.
  - Exits never consume entry capacity.
  - Pending entries reserve a slot until filled or cancelled.
  - EOD exits must never be blocked.
"""

from __future__ import annotations

import pytest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from app.brokers.broker_interface import OrderRequest, OrderSide, OrderType
from app.config import Settings
from app.risk import RiskCheck, RiskManager

ET = ZoneInfo("America/New_York")
EQUITY = Decimal("100_000")
SAFE_NOW = datetime(2024, 1, 2, 10, 0, tzinfo=ET)


def _make_settings(**kwargs) -> Settings:
    defaults = dict(
        live_trading_enabled=False,
        broker="paper",
        database_url="sqlite+aiosqlite:///:memory:",
        kill_switch_file="./TEST_KILL_ENTRY_SEM",
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


# ── 1. Fill increments entries_today ─────────────────────────────────────────

def test_fill_increments_entries_today():
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)
    assert rm.entries_today == 0
    rm.record_entry_pending()
    assert rm.entries_today == 0
    assert rm.pending_entries == 1
    rm.record_entry_filled()
    assert rm.entries_today == 1
    assert rm.pending_entries == 0


# ── 2. Exit does NOT increment entries_today ──────────────────────────────────

def test_exit_does_not_increment_entries(mock_option_contract):
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)
    rm.record_entry_pending()
    rm.record_entry_filled()
    assert rm.entries_today == 1

    rm.record_exit(pnl=Decimal("-50"))
    assert rm.entries_today == 1
    assert rm.exits_today == 1

    # Still room to take another entry
    result = rm.check_order(_make_order(), EQUITY, mock_option_contract, now=SAFE_NOW)
    assert RiskCheck.MAX_TRADES_PER_DAY not in result.failed_checks


# ── 3. EOD exit does NOT consume entry capacity ───────────────────────────────

def test_eod_exit_does_not_block_further_entries(mock_option_contract):
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)

    # Two entries filled
    for _ in range(2):
        rm.record_entry_pending()
        rm.record_entry_filled()

    # Both close EOD
    rm.record_exit(pnl=Decimal("-10"))
    rm.record_exit(pnl=Decimal("-20"))

    assert rm.entries_today == 2
    assert rm.exits_today == 2
    assert rm.pending_entries == 0

    # Third entry is still allowed (max=3)
    result = rm.check_order(_make_order(), EQUITY, mock_option_contract, now=SAFE_NOW)
    assert RiskCheck.MAX_TRADES_PER_DAY not in result.failed_checks


# ── 4. Cancelled entry frees the pending slot ─────────────────────────────────

def test_cancelled_entry_frees_slot(mock_option_contract):
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)

    # Use all 3 slots with pending (unfilled) entries
    for _ in range(3):
        rm.record_entry_pending()
    assert rm.pending_entries == 3

    # All three are at limit
    result = rm.check_order(_make_order(), EQUITY, mock_option_contract, now=SAFE_NOW)
    assert RiskCheck.MAX_TRADES_PER_DAY in result.failed_checks

    # Cancel one
    rm.record_entry_cancelled()
    assert rm.pending_entries == 2

    # Now one slot is free
    result = rm.check_order(_make_order(), EQUITY, mock_option_contract, now=SAFE_NOW)
    assert RiskCheck.MAX_TRADES_PER_DAY not in result.failed_checks


# ── 5. Pending entries count against capacity ─────────────────────────────────

def test_pending_entries_count_against_capacity(mock_option_contract):
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)

    # One filled, two pending
    rm.record_entry_pending()
    rm.record_entry_filled()
    rm.record_entry_pending()
    rm.record_entry_pending()

    assert rm.entries_today == 1
    assert rm.pending_entries == 2

    # capacity_used = 1 + 2 = 3 = max → blocked
    result = rm.check_order(_make_order(), EQUITY, mock_option_contract, now=SAFE_NOW)
    assert not result.passed
    assert RiskCheck.MAX_TRADES_PER_DAY in result.failed_checks


# ── 6. max=3 allows three entries plus unlimited exits ────────────────────────

def test_max3_allows_three_entries_and_unlimited_exits(mock_option_contract):
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)

    for _ in range(3):
        rm.record_entry_pending()
        rm.record_entry_filled()
        rm.record_exit(pnl=Decimal("-5"))

    assert rm.entries_today == 3
    assert rm.exits_today == 3
    assert rm.pending_entries == 0

    # Fourth entry is blocked
    result = rm.check_order(_make_order(), EQUITY, mock_option_contract, now=SAFE_NOW)
    assert not result.passed
    assert RiskCheck.MAX_TRADES_PER_DAY in result.failed_checks


# ── 7. ORB not blocked by prior VWAP exit ────────────────────────────────────

def test_orb_not_blocked_by_vwap_exit(mock_option_contract):
    """Reproduce the 2026-05-29 bug: exits should not saturate the entry counter."""
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)

    # VWAP trade 1: entry + exit
    rm.record_entry_pending()
    rm.record_entry_filled()
    rm.record_exit(pnl=Decimal("-31.50"))

    # VWAP trade 2: entry + exit
    rm.record_entry_pending()
    rm.record_entry_filled()
    rm.record_exit(pnl=Decimal("-9.00"))

    assert rm.entries_today == 2
    assert rm.exits_today == 2
    assert rm.pending_entries == 0

    # ORB entry should still be allowed (only 2 of 3 entries used)
    result = rm.check_order(_make_order(), EQUITY, mock_option_contract, now=SAFE_NOW)
    assert RiskCheck.MAX_TRADES_PER_DAY not in result.failed_checks


# ── 8. Report labels are clear ────────────────────────────────────────────────

def test_report_labels_distinguish_entries_exits():
    rm = RiskManager(_make_settings())
    rm.start_session(EQUITY)

    rm.record_entry_pending()
    rm.record_entry_filled()
    rm.record_exit(pnl=Decimal("-20"))

    assert rm.entries_today == 1
    assert rm.exits_today == 1
    assert rm.trades_today == 1      # backward-compat alias
    assert rm.pending_entries == 0
    assert float(rm.daily_pnl) == -20.0
