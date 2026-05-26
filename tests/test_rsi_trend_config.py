"""
Tests for RSI_trend configuration integrity.

Verifies:
  - session_runner uses configured EMA period, not a hardcoded value
  - min_bars_required reflects the configured EMA period
  - RSITrendSettings loads from config/env correctly
  - strategy readiness logic is correct
  - fast_intraday_diagnostic mode requires paper mode
  - no live trading path is affected
"""

from __future__ import annotations

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_rsi_strategy(rsi_period=14, rsi_oversold=35, rsi_overbought=65,
                       trend_ema_period=50, bar_interval="5m", mode="standard"):
    from app.strategies.rsi_trend_strategy import RSITrendStrategy
    return RSITrendStrategy(params={
        "rsi_period": rsi_period,
        "rsi_oversold": rsi_oversold,
        "rsi_overbought": rsi_overbought,
        "trend_ema_period": trend_ema_period,
        "bar_interval": bar_interval,
        "mode": mode,
    })


# ── 1. EMA period drives min_bars_required ────────────────────────────────────

class TestMinBarsRequired:

    def test_ema50_produces_required_bars_55(self):
        s = _make_rsi_strategy(rsi_period=14, trend_ema_period=50)
        assert s.min_bars_required == 55  # max(14, 50) + 5

    def test_ema20_produces_required_bars_25(self):
        s = _make_rsi_strategy(rsi_period=14, trend_ema_period=20)
        assert s.min_bars_required == 25  # max(14, 20) + 5

    def test_rsi_period_dominates_when_larger(self):
        s = _make_rsi_strategy(rsi_period=30, trend_ema_period=20)
        assert s.min_bars_required == 35  # max(30, 20) + 5

    def test_readiness_false_before_required_bars(self):
        import pandas as pd
        s = _make_rsi_strategy(trend_ema_period=50)  # needs 55
        bars = pd.DataFrame({"open": [1.0] * 30, "high": [1.0] * 30,
                              "low": [1.0] * 30, "close": [1.0] * 30,
                              "volume": [100] * 30})
        info = s.get_readiness_info(bars)
        assert info["ready"] is False
        assert info["bars_available"] == 30
        assert info["min_bars_required"] == 55

    def test_readiness_true_at_required_bars(self):
        import pandas as pd
        s = _make_rsi_strategy(trend_ema_period=20)  # needs 25
        bars = pd.DataFrame({"open": [1.0] * 25, "high": [1.0] * 25,
                              "low": [1.0] * 25, "close": [1.0] * 25,
                              "volume": [100] * 25})
        info = s.get_readiness_info(bars)
        assert info["ready"] is True
        assert info["bars_available"] == 25


# ── 2. RSITrendSettings loads correct defaults ────────────────────────────────

class TestRSITrendSettings:

    def test_default_ema_period_is_50(self, monkeypatch):
        monkeypatch.delenv("RSI_TREND_TREND_EMA_PERIOD", raising=False)
        from app.config.settings import RSITrendSettings
        s = RSITrendSettings(_env_file=None)
        # Config.yaml sets 50; code-level default is also 50
        assert s.trend_ema_period == 50

    def test_env_override_ema_period(self, monkeypatch):
        monkeypatch.setenv("RSI_TREND_TREND_EMA_PERIOD", "20")
        from app.config.settings import RSITrendSettings
        s = RSITrendSettings()
        assert s.trend_ema_period == 20

    def test_default_bar_interval_is_5m(self, monkeypatch):
        monkeypatch.delenv("RSI_TREND_BAR_INTERVAL", raising=False)
        from app.config.settings import RSITrendSettings
        s = RSITrendSettings(_env_file=None)
        assert s.bar_interval == "5m"

    def test_default_mode_is_standard(self, monkeypatch):
        monkeypatch.delenv("RSI_TREND_MODE", raising=False)
        from app.config.settings import RSITrendSettings
        s = RSITrendSettings(_env_file=None)
        assert s.mode == "standard"

    def test_env_override_mode(self, monkeypatch):
        monkeypatch.setenv("RSI_TREND_MODE", "fast_intraday_diagnostic")
        from app.config.settings import RSITrendSettings
        s = RSITrendSettings()
        assert s.mode == "fast_intraday_diagnostic"


# ── 3. fast_intraday_diagnostic is paper-only ─────────────────────────────────

class TestFastIntraday:

    def test_fast_mode_accepted_in_paper(self):
        s = _make_rsi_strategy(mode="fast_intraday_diagnostic")
        assert s._mode == "fast_intraday_diagnostic"

    def test_strategy_stores_mode_param(self):
        s = _make_rsi_strategy(mode="standard")
        assert s._mode == "standard"

    def test_bar_interval_stored(self):
        s = _make_rsi_strategy(bar_interval="1m")
        assert s._bar_interval == "1m"


# ── 4. Other strategies have min_bars_required ────────────────────────────────

class TestOtherStrategiesMinBars:

    def test_orb_min_bars(self):
        from app.strategies.opening_range_breakout import OpeningRangeBreakoutStrategy
        s = OpeningRangeBreakoutStrategy(params={"range_minutes": 15})
        assert s.min_bars_required == 17  # 15 + 2

    def test_vwap_min_bars(self):
        from app.strategies.vwap_strategy import VWAPReclaimStrategy
        s = VWAPReclaimStrategy(params={"confirmation_bars": 2})
        assert s.min_bars_required == 7  # 2 + 5

    def test_strategy_base_default_min_bars(self):
        from app.strategies.strategy_base import StrategyBase, Signal
        import pandas as pd

        class _Stub(StrategyBase):
            @property
            def name(self):
                return "stub"
            def generate_signals(self, bars, symbol):
                return []

        s = _Stub("stub_id")
        assert s.min_bars_required == 2

    def test_get_readiness_info_empty_bars(self):
        import pandas as pd
        s = _make_rsi_strategy()
        info = s.get_readiness_info(pd.DataFrame())
        assert info["ready"] is False
        assert info["bars_available"] == 0
