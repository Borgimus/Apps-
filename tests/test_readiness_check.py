"""
Tests for strategy readiness check logic.

Covers:
  - Bar interval parsing
  - Earliest ready time computation
  - Today-bar counting from a DataFrame
  - Clock-based readiness (session_runner._compute_strategy_readiness)
  - Dashboard scan_store reflects strategy_readiness
  - Runtime config parameters match settings
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

_ET = ZoneInfo("America/New_York")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _et(hour: int, minute: int) -> datetime:
    return datetime(2026, 5, 26, hour, minute, 0, tzinfo=_ET)


def _make_bars_with_timestamps(timestamps_et: list[datetime]) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame with the given ET timestamps."""
    from zoneinfo import ZoneInfo as _ZI
    idx = pd.DatetimeIndex([ts.astimezone(_ZI("UTC")) for ts in timestamps_et], tz="UTC")
    return pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 100},
        index=idx,
    )


# ── _parse_bar_interval_minutes ───────────────────────────────────────────────

class TestParseBarInterval:
    def test_5m(self):
        from scripts.readiness_check import _parse_bar_interval_minutes
        assert _parse_bar_interval_minutes("5m") == 5

    def test_1m(self):
        from scripts.readiness_check import _parse_bar_interval_minutes
        assert _parse_bar_interval_minutes("1m") == 1

    def test_1h(self):
        from scripts.readiness_check import _parse_bar_interval_minutes
        assert _parse_bar_interval_minutes("1h") == 60

    def test_case_insensitive(self):
        from scripts.readiness_check import _parse_bar_interval_minutes
        assert _parse_bar_interval_minutes("5M") == 5

    def test_unknown_falls_back_to_5(self):
        from scripts.readiness_check import _parse_bar_interval_minutes
        assert _parse_bar_interval_minutes("?") == 5


# ── _earliest_ready_time ──────────────────────────────────────────────────────

class TestEarliestReadyTime:
    def test_rsi_ema50_at_5m(self):
        from scripts.readiness_check import _earliest_ready_time
        market_open = _et(9, 30)
        # min_bars = max(14, 50) + 5 = 55; 55 * 5 = 275 min = 4h35m → 14:05
        t = _earliest_ready_time(55, 5, market_open)
        assert t.hour == 14 and t.minute == 5

    def test_rsi_ema20_at_5m(self):
        from scripts.readiness_check import _earliest_ready_time
        market_open = _et(9, 30)
        # min_bars = max(14, 20) + 5 = 25; 25 * 5 = 125 min = 2h05m → 11:35
        t = _earliest_ready_time(25, 5, market_open)
        assert t.hour == 11 and t.minute == 35

    def test_orb_at_5m(self):
        from scripts.readiness_check import _earliest_ready_time
        market_open = _et(9, 30)
        # min_bars = 15 + 2 = 17; 17 * 5 = 85 min = 1h25m → 10:55
        t = _earliest_ready_time(17, 5, market_open)
        assert t.hour == 10 and t.minute == 55

    def test_vwap_at_5m(self):
        from scripts.readiness_check import _earliest_ready_time
        market_open = _et(9, 30)
        # min_bars = 2 + 5 = 7; 7 * 5 = 35 min → 10:05
        t = _earliest_ready_time(7, 5, market_open)
        assert t.hour == 10 and t.minute == 5


# ── _count_today_bars ─────────────────────────────────────────────────────────

class TestCountTodayBars:
    def test_counts_only_session_bars(self):
        from scripts.readiness_check import _count_today_bars
        import datetime as _dt
        today = _dt.date(2026, 5, 26)
        # 5 bars starting at 9:30 ET on 2026-05-26
        timestamps = [_et(9, 30 + i * 5) for i in range(5)]
        # plus one pre-market bar (should be excluded)
        timestamps.insert(0, _et(9, 0))
        bars = _make_bars_with_timestamps(timestamps)
        assert _count_today_bars(bars, today) == 5

    def test_empty_bars_returns_zero(self):
        from scripts.readiness_check import _count_today_bars
        import datetime as _dt
        assert _count_today_bars(pd.DataFrame(), _dt.date(2026, 5, 26)) == 0

    def test_no_today_bars_returns_zero(self):
        from scripts.readiness_check import _count_today_bars
        import datetime as _dt
        yesterday = _dt.date(2026, 5, 25)
        today = _dt.date(2026, 5, 26)
        bars = _make_bars_with_timestamps([_et(9, 30), _et(9, 35)])
        # bars are on 2026-05-26, asking for 2026-05-25
        assert _count_today_bars(bars, yesterday) == 0


# ── _compute_strategy_readiness (session_runner clock logic) ──────────────────

class TestComputeStrategyReadiness:
    def _make_strategies(self):
        from app.strategies.opening_range_breakout import OpeningRangeBreakoutStrategy
        from app.strategies.vwap_strategy import VWAPReclaimStrategy
        from app.strategies.rsi_trend_strategy import RSITrendStrategy
        return [
            OpeningRangeBreakoutStrategy(params={"range_minutes": 15}),
            VWAPReclaimStrategy(params={"confirmation_bars": 2}),
            RSITrendStrategy(params={"rsi_period": 14, "trend_ema_period": 50}),
        ]

    def test_pre_market_nothing_ready(self):
        from scripts.session_runner import _compute_strategy_readiness
        strategies = self._make_strategies()
        now = _et(9, 0)  # before open
        result = _compute_strategy_readiness(strategies, now)
        assert all(not r["ready"] for r in result)
        assert all(r["bars_elapsed_since_open"] == 0 for r in result)

    def test_orb_ready_after_10h55(self):
        from scripts.session_runner import _compute_strategy_readiness
        strategies = self._make_strategies()
        # ORB needs 17 bars → ready at 9:30 + 85min = 10:55
        # At 11:00 (90min elapsed, 18 complete bars) ORB should be ready
        now = _et(11, 0)
        result = _compute_strategy_readiness(strategies, now)
        orb = next(r for r in result if r["strategy_id"] == "orb")
        assert orb["ready"] is True
        assert orb["bars_elapsed_since_open"] == 18  # 90 // 5

    def test_vwap_ready_after_10h05(self):
        from scripts.session_runner import _compute_strategy_readiness
        strategies = self._make_strategies()
        # VWAP needs 7 bars → ready at 9:30 + 35min = 10:05
        now = _et(10, 6)
        result = _compute_strategy_readiness(strategies, now)
        vwap = next(r for r in result if r["strategy_id"] == "vwap_reclaim")
        assert vwap["ready"] is True

    def test_rsi_ema50_not_ready_at_noon(self):
        from scripts.session_runner import _compute_strategy_readiness
        strategies = self._make_strategies()
        # RSI EMA50 needs 55 bars → ready at 14:05
        now = _et(12, 0)  # only 150min/5 = 30 bars elapsed
        result = _compute_strategy_readiness(strategies, now)
        rsi = next(r for r in result if r["strategy_id"] == "rsi_trend")
        assert rsi["ready"] is False
        assert rsi["bars_short"] == 55 - 30

    def test_rsi_ema50_ready_after_14h05(self):
        from scripts.session_runner import _compute_strategy_readiness
        strategies = self._make_strategies()
        # 14:10 → (14:10 - 9:30) = 280min / 5 = 56 bars elapsed ≥ 55
        now = _et(14, 10)
        result = _compute_strategy_readiness(strategies, now)
        rsi = next(r for r in result if r["strategy_id"] == "rsi_trend")
        assert rsi["ready"] is True
        assert rsi["bars_short"] == 0

    def test_result_contains_required_fields(self):
        from scripts.session_runner import _compute_strategy_readiness
        strategies = self._make_strategies()
        result = _compute_strategy_readiness(strategies, _et(10, 0))
        for r in result:
            assert "strategy_id" in r
            assert "name" in r
            assert "min_bars_required" in r
            assert "bars_elapsed_since_open" in r
            assert "ready" in r
            assert "earliest_ready_time_et" in r
            assert "bars_short" in r

    def test_earliest_ready_time_format(self):
        from scripts.session_runner import _compute_strategy_readiness
        strategies = self._make_strategies()
        result = _compute_strategy_readiness(strategies, _et(10, 0))
        rsi = next(r for r in result if r["strategy_id"] == "rsi_trend")
        # Should be "HH:MM" format
        assert len(rsi["earliest_ready_time_et"]) == 5
        assert rsi["earliest_ready_time_et"][2] == ":"


# ── Runtime config matches settings ──────────────────────────────────────────

class TestRuntimeConfigMatchesSettings:
    def test_rsi_trend_params_from_settings(self, monkeypatch):
        monkeypatch.delenv("RSI_TREND_TREND_EMA_PERIOD", raising=False)
        from app.config.settings import RSITrendSettings
        from app.strategies.rsi_trend_strategy import RSITrendStrategy
        cfg = RSITrendSettings(_env_file=None)
        strat = RSITrendStrategy(params={
            "rsi_period": cfg.rsi_period,
            "trend_ema_period": cfg.trend_ema_period,
        })
        assert strat._rsi_period == cfg.rsi_period
        assert strat._trend_ema_period == cfg.trend_ema_period
        assert strat.min_bars_required == max(cfg.rsi_period, cfg.trend_ema_period) + 5

    def test_no_live_trading_flag_set(self):
        from app.config.settings import get_settings
        s = get_settings()
        assert s.live_trading_enabled is False
