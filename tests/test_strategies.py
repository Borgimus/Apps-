"""Tests for strategy signal generation."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from app.strategies import (
    MACompressionStrategy,
    OpeningRangeBreakoutStrategy,
    RSITrendStrategy,
    VWAPReclaimStrategy,
)
from app.strategies.strategy_base import SignalDirection


# ── Opening Range Breakout ────────────────────────────────────────────────────

class TestOpeningRangeBreakout:

    def _make_orb_bars(self, direction: str = "up") -> pd.DataFrame:
        """Bars with a clear opening range then breakout."""
        # 5-min bars, single day, market open at 9:30 ET = 14:30 UTC
        n = 40
        idx = pd.date_range(
            "2024-01-02 14:30", periods=n, freq="5min", tz="UTC"
        )
        base = 450.0
        prices = [base] * n
        volumes = [1_000_000] * n

        # First 3 bars: tight range (opening range = 15 min at 5-min bars)
        for i in range(3):
            prices[i] = base + np.random.uniform(-0.2, 0.2)

        # Bar 4: big breakout candle with high volume
        if direction == "up":
            prices[4] = base + 2.0  # close above OR high
            volumes[4] = 3_000_000
        else:
            prices[4] = base - 2.0  # close below OR low
            volumes[4] = 3_000_000

        df = pd.DataFrame(
            {
                "open": [p - 0.1 for p in prices],
                "high": [p + 0.3 for p in prices],
                "low": [p - 0.3 for p in prices],
                "close": prices,
                "volume": volumes,
            },
            index=idx,
        )
        return df

    def test_upside_breakout_generates_long_signal(self):
        strat = OpeningRangeBreakoutStrategy(
            params={"range_minutes": 15, "min_range_pts": 0.1, "volume_confirmation": True}
        )
        bars = self._make_orb_bars("up")
        signals = strat.generate_signals(bars, "SPY")
        long_signals = [s for s in signals if s.direction == SignalDirection.LONG]
        assert len(long_signals) >= 1

    def test_downside_breakout_generates_short_signal(self):
        strat = OpeningRangeBreakoutStrategy(
            params={"range_minutes": 15, "min_range_pts": 0.1, "volume_confirmation": True}
        )
        bars = self._make_orb_bars("down")
        signals = strat.generate_signals(bars, "SPY")
        short_signals = [s for s in signals if s.direction == SignalDirection.SHORT]
        assert len(short_signals) >= 1

    def test_narrow_range_no_signal(self):
        strat = OpeningRangeBreakoutStrategy(
            params={"range_minutes": 15, "min_range_pts": 10.0, "volume_confirmation": False}
        )
        bars = self._make_orb_bars("up")  # range is ~0.6 pts, threshold is 10
        signals = strat.generate_signals(bars, "SPY")
        assert signals == []

    def test_empty_bars_returns_empty(self):
        strat = OpeningRangeBreakoutStrategy()
        signals = strat.generate_signals(pd.DataFrame(), "SPY")
        assert signals == []

    def test_signal_symbol_matches(self):
        strat = OpeningRangeBreakoutStrategy(
            params={"range_minutes": 15, "min_range_pts": 0.1}
        )
        bars = self._make_orb_bars("up")
        signals = strat.generate_signals(bars, "QQQ")
        for s in signals:
            assert s.symbol == "QQQ"


# ── RSI + Trend ───────────────────────────────────────────────────────────────

class TestRSITrendStrategy:

    def _make_oversold_bars(self) -> pd.DataFrame:
        """Bars that go oversold then bounce."""
        n = 80
        idx = pd.date_range("2024-01-02", periods=n, freq="B", tz="UTC")
        # Trending up (price above EMA) with a temporary dip to oversold
        prices = np.linspace(400, 460, n)
        # Add a sharp dip and recovery in the middle
        prices[35:45] = np.linspace(415, 395, 10)
        prices[45:55] = np.linspace(395, 420, 10)
        prices[55:] = np.linspace(420, 460, n - 55)
        df = pd.DataFrame(
            {
                "open": prices - 0.5,
                "high": prices + 1.0,
                "low": prices - 1.0,
                "close": prices,
                "volume": [5_000_000] * n,
            },
            index=idx,
        )
        return df

    def test_oversold_bounce_generates_long(self):
        strat = RSITrendStrategy(params={"rsi_period": 14, "rsi_oversold": 40, "trend_ema_period": 20})
        bars = self._make_oversold_bars()
        signals = strat.generate_signals(bars, "SPY")
        # Should generate at least one LONG when RSI crosses back above oversold
        long_signals = [s for s in signals if s.direction == SignalDirection.LONG]
        assert len(long_signals) >= 1

    def test_signals_contain_rsi_in_metadata(self):
        strat = RSITrendStrategy(params={"rsi_period": 14, "rsi_oversold": 40, "trend_ema_period": 20})
        bars = self._make_oversold_bars()
        signals = strat.generate_signals(bars, "SPY")
        for s in signals:
            assert "rsi" in s.metadata
            assert "ema" in s.metadata

    def test_insufficient_bars_returns_empty(self):
        strat = RSITrendStrategy()
        bars = pd.DataFrame({"open": [1], "high": [1], "low": [1], "close": [1], "volume": [1]})
        signals = strat.generate_signals(bars, "SPY")
        assert signals == []


# ── VWAP ──────────────────────────────────────────────────────────────────────

class TestVWAPReclaimStrategy:

    def _make_vwap_reclaim_bars(self) -> pd.DataFrame:
        """Bars where price dips below VWAP then reclaims it."""
        n = 40
        idx = pd.date_range(
            "2024-01-02 14:30", periods=n, freq="5min", tz="UTC"
        )
        prices = [450.0] * n
        # Price below VWAP for bars 10-15, then above
        for i in range(10, 15):
            prices[i] = 448.0
        for i in range(15, 25):
            prices[i] = 451.0  # above VWAP

        df = pd.DataFrame(
            {
                "open": [p - 0.1 for p in prices],
                "high": [p + 0.2 for p in prices],
                "low": [p - 0.2 for p in prices],
                "close": prices,
                "volume": [2_000_000] * n,
            },
            index=idx,
        )
        return df

    def test_returns_signals(self):
        strat = VWAPReclaimStrategy(
            params={"proximity_pct": 0.01, "confirmation_bars": 1}
        )
        bars = self._make_vwap_reclaim_bars()
        signals = strat.generate_signals(bars, "SPY")
        assert isinstance(signals, list)

    def test_empty_bars_returns_empty(self):
        strat = VWAPReclaimStrategy()
        signals = strat.generate_signals(pd.DataFrame(), "SPY")
        assert signals == []


# ── MA Compression ────────────────────────────────────────────────────────────

class TestMACompressionStrategy:

    def _make_compressed_then_breakout_bars(self) -> pd.DataFrame:
        n = 80
        idx = pd.date_range("2024-01-02", periods=n, freq="B", tz="UTC")
        # Flat for 30 bars (compression), then breakout
        prices = [450.0] * n
        for i in range(30, 35):
            prices[i] = 450.0 + (i - 30) * 0.1  # slight drift in compression
        for i in range(35, n):
            prices[i] = 450.0 + (i - 30) * 1.0  # strong breakout

        df = pd.DataFrame(
            {
                "open": [p - 0.1 for p in prices],
                "high": [p + max(0.5, (i - 30) * 0.3) for i, p in enumerate(prices)],
                "low": [p - 0.1 for p in prices],
                "close": prices,
                "volume": [2_000_000] * n,
            },
            index=idx,
        )
        return df

    def test_breakout_after_compression_generates_long(self):
        strat = MACompressionStrategy(
            params={
                "fast_period": 5,
                "slow_period": 8,
                "compression_threshold_pct": 0.005,
                "min_compression_bars": 3,
            }
        )
        bars = self._make_compressed_then_breakout_bars()
        signals = strat.generate_signals(bars, "SPY")
        long_signals = [s for s in signals if s.direction == SignalDirection.LONG]
        assert len(long_signals) >= 1

    def test_metadata_contains_ema_values(self):
        strat = MACompressionStrategy(
            params={"fast_period": 5, "slow_period": 8, "compression_threshold_pct": 0.005}
        )
        bars = self._make_compressed_then_breakout_bars()
        signals = strat.generate_signals(bars, "SPY")
        for s in signals:
            assert "fast_ema" in s.metadata
            assert "slow_ema" in s.metadata
