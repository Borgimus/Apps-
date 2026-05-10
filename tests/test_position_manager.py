"""
Tests for PositionManager.

Covers:
  - open / close lifecycle
  - stop loss trigger
  - take profit trigger
  - trailing stop trigger
  - max hold time trigger
  - EOD forced exit
  - dedup guard (has_position_for_symbol)
  - loss cooldown
  - update_price peak tracking
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.trading.position_manager import OpenPosition, PositionManager

ET = ZoneInfo("America/New_York")


def _settings(
    stop_loss_pct=0.50,
    take_profit_pct=1.00,
    trailing_stop_pct=0.25,
    max_hold_minutes=120,
    eod_exit_time="15:45",
    cooldown_after_loss_minutes=15,
):
    s = MagicMock()
    s.position.stop_loss_pct = stop_loss_pct
    s.position.take_profit_pct = take_profit_pct
    s.position.trailing_stop_pct = trailing_stop_pct
    s.position.max_hold_minutes = max_hold_minutes
    s.position.eod_exit_time = eod_exit_time
    s.position.cooldown_after_loss_minutes = cooldown_after_loss_minutes
    return s


def _pm(**kwargs):
    return PositionManager(settings=_settings(**kwargs))


def _open(pm, symbol="SPY", opt_sym="SPY_OPT_1", entry_price=3.00, direction="LONG"):
    entry_time = datetime(2024, 1, 2, 10, 0, 0, tzinfo=ET)
    pos = pm.open(
        option_symbol=opt_sym,
        symbol=symbol,
        strategy_id="orb",
        direction=direction,
        entry_time=entry_time,
        entry_price=entry_price,
        quantity=1,
    )
    return pos, entry_time


class TestLifecycle:

    def test_open_registers_position(self):
        pm = _pm()
        _open(pm)
        assert pm.has_position("SPY_OPT_1")

    def test_open_sets_peak_to_entry(self):
        pm = _pm()
        pos, _ = _open(pm, entry_price=3.00)
        assert pos.peak_price == 3.00

    def test_close_removes_position(self):
        pm = _pm()
        _open(pm)
        pm.close("SPY_OPT_1", exit_price=4.00, pnl=100.0)
        assert not pm.has_position("SPY_OPT_1")

    def test_close_returns_position(self):
        pm = _pm()
        _open(pm)
        closed = pm.close("SPY_OPT_1", exit_price=4.00, pnl=100.0)
        assert closed is not None
        assert closed.option_symbol == "SPY_OPT_1"

    def test_close_unknown_returns_none(self):
        pm = _pm()
        result = pm.close("NONEXISTENT", exit_price=1.0, pnl=0.0)
        assert result is None

    def test_has_position_for_symbol(self):
        pm = _pm()
        _open(pm, symbol="SPY", opt_sym="SPY_OPT_1")
        assert pm.has_position_for_symbol("SPY")
        assert not pm.has_position_for_symbol("QQQ")

    def test_open_positions_list(self):
        pm = _pm()
        _open(pm, opt_sym="A")
        _open(pm, symbol="QQQ", opt_sym="B")
        assert len(pm.open_positions()) == 2


class TestExitConditions:

    def _now(self, hour=11, minute=0):
        return datetime(2024, 1, 2, hour, minute, 0, tzinfo=ET)

    def test_no_exit_at_entry(self):
        pm = _pm()
        _open(pm, entry_price=3.00)
        result = pm.should_exit("SPY_OPT_1", 3.00, self._now())
        assert result is None

    def test_stop_loss_triggers(self):
        pm = _pm(stop_loss_pct=0.50)
        _open(pm, entry_price=4.00)
        # 4.00 × (1 - 0.50) = 2.00; price at 1.99 should trigger
        result = pm.should_exit("SPY_OPT_1", 1.99, self._now())
        assert result == "stop_loss"

    def test_stop_loss_not_triggered_above_threshold(self):
        # Disable trailing stop (set to 100%) so only stop-loss logic is tested
        pm = _pm(stop_loss_pct=0.50, trailing_stop_pct=1.00)
        _open(pm, entry_price=4.00)
        # 4.00 * (1 - 0.50) = 2.00; price at 2.01 is above threshold
        result = pm.should_exit("SPY_OPT_1", 2.01, self._now())
        assert result is None

    def test_take_profit_triggers(self):
        pm = _pm(take_profit_pct=1.00)
        _open(pm, entry_price=3.00)
        # 3.00 × (1 + 1.00) = 6.00; price at 6.01 should trigger
        result = pm.should_exit("SPY_OPT_1", 6.01, self._now())
        assert result == "take_profit"

    def test_take_profit_not_triggered_below(self):
        pm = _pm(take_profit_pct=1.00)
        _open(pm, entry_price=3.00)
        result = pm.should_exit("SPY_OPT_1", 5.99, self._now())
        assert result is None

    def test_trailing_stop_triggers_after_peak(self):
        pm = _pm(trailing_stop_pct=0.25)
        _open(pm, entry_price=2.00)
        pm.update_price("SPY_OPT_1", 4.00)   # peak = 4.00
        # 4.00 × (1 - 0.25) = 3.00; price at 2.99 should trigger
        result = pm.should_exit("SPY_OPT_1", 2.99, self._now())
        assert result == "trailing_stop"

    def test_trailing_stop_not_triggered_above_trail(self):
        pm = _pm(trailing_stop_pct=0.25)
        _open(pm, entry_price=2.00)
        pm.update_price("SPY_OPT_1", 4.00)
        result = pm.should_exit("SPY_OPT_1", 3.01, self._now())
        assert result is None

    def test_max_hold_triggers(self):
        pm = _pm(max_hold_minutes=60)
        pos, entry_time = _open(pm, entry_price=3.00)
        too_late = entry_time + timedelta(minutes=61)
        result = pm.should_exit("SPY_OPT_1", 3.00, too_late)
        assert result == "max_hold"

    def test_max_hold_not_triggered_before_limit(self):
        pm = _pm(max_hold_minutes=60)
        _open(pm, entry_price=3.00)
        slightly_early = datetime(2024, 1, 2, 10, 59, 0, tzinfo=ET)
        result = pm.should_exit("SPY_OPT_1", 3.00, slightly_early)
        assert result is None

    def test_eod_exit_triggers(self):
        pm = _pm(eod_exit_time="15:45", max_hold_minutes=999)
        _open(pm, entry_price=3.00)
        at_eod = datetime(2024, 1, 2, 15, 45, 0, tzinfo=ET)
        result = pm.should_exit("SPY_OPT_1", 3.00, at_eod)
        assert result == "eod_exit"

    def test_eod_exit_not_triggered_before(self):
        pm = _pm(eod_exit_time="15:45", max_hold_minutes=999)
        _open(pm, entry_price=3.00)
        before_eod = datetime(2024, 1, 2, 15, 44, 0, tzinfo=ET)
        result = pm.should_exit("SPY_OPT_1", 3.00, before_eod)
        assert result is None

    def test_unknown_symbol_returns_none(self):
        pm = _pm()
        result = pm.should_exit("UNKNOWN", 3.00, self._now())
        assert result is None


class TestUpdatePrice:

    def test_update_raises_peak(self):
        pm = _pm()
        _open(pm, entry_price=3.00)
        pm.update_price("SPY_OPT_1", 5.00)
        assert pm._positions["SPY_OPT_1"].peak_price == 5.00

    def test_update_does_not_lower_peak(self):
        pm = _pm()
        _open(pm, entry_price=3.00)
        pm.update_price("SPY_OPT_1", 5.00)
        pm.update_price("SPY_OPT_1", 1.00)
        assert pm._positions["SPY_OPT_1"].peak_price == 5.00


class TestCooldown:

    def test_no_cooldown_initially(self):
        pm = _pm(cooldown_after_loss_minutes=15)
        now = datetime(2024, 1, 2, 11, 0, 0, tzinfo=ET)
        assert not pm.is_in_cooldown(now)

    def test_cooldown_activates_after_loss(self):
        pm = _pm(cooldown_after_loss_minutes=15)
        _open(pm)
        pm.close("SPY_OPT_1", exit_price=1.0, pnl=-100.0)
        now = datetime.now(tz=ET)
        assert pm.is_in_cooldown(now)

    def test_no_cooldown_after_win(self):
        pm = _pm(cooldown_after_loss_minutes=15)
        _open(pm)
        pm.close("SPY_OPT_1", exit_price=6.0, pnl=300.0)
        now = datetime.now(tz=ET)
        assert not pm.is_in_cooldown(now)

    def test_cooldown_expires(self):
        pm = _pm(cooldown_after_loss_minutes=0)
        _open(pm)
        pm.close("SPY_OPT_1", exit_price=1.0, pnl=-100.0)
        # With 0-minute cooldown the window is instantly expired
        now = datetime.now(tz=ET) + timedelta(seconds=1)
        assert not pm.is_in_cooldown(now)
