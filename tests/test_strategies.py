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
        """
        Bars engineered to push RSI(14) clearly below the oversold threshold
        and then recover above it, while staying above the trend EMA.

        Structure:
          bars  0-29 : gentle uptrend from 400 → 430 (EMA warm-up, bullish bias)
          bars 30-44 : sharp crash from 430 → 370  (15 bars, ~14% drop → RSI well below 35)
          bars 45-79 : strong recovery 370 → 440    (price reclaims and exceeds EMA)
        """
        n = 80
        idx = pd.date_range("2024-01-02", periods=n, freq="B", tz="UTC")
        prices = np.empty(n)
        prices[:30] = np.linspace(400, 430, 30)
        prices[30:45] = np.linspace(430, 370, 15)   # severe drop
        prices[45:] = np.linspace(370, 440, n - 45)  # full recovery above EMA
        df = pd.DataFrame(
            {
                "open": prices - 0.5,
                "high": prices + 1.5,
                "low": prices - 1.5,
                "close": prices,
                "volume": [5_000_000] * n,
            },
            index=idx,
        )
        return df

    def test_oversold_bounce_generates_long(self):
        # EMA(5) tracks the recovery quickly enough that price is above it
        # when RSI crosses back above the oversold threshold — a realistic
        # short-term trend filter for intraday / swing setups.
        strat = RSITrendStrategy(params={"rsi_period": 14, "rsi_oversold": 40, "trend_ema_period": 5})
        bars = self._make_oversold_bars()
        signals = strat.generate_signals(bars, "SPY")
        long_signals = [s for s in signals if s.direction == SignalDirection.LONG]
        assert len(long_signals) >= 1

    def test_signals_contain_rsi_in_metadata(self):
        strat = RSITrendStrategy(params={"rsi_period": 14, "rsi_oversold": 40, "trend_ema_period": 5})
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
        """
        40 bars of tight compression at 450, then a decisive breakout.

        The compression candles have a tiny range (0.2) so avg_range ≈ 0.2.
        The breakout candle has range = 2.0, which is 10× avg_range and easily
        satisfies the 1.5× threshold.  The close jumps above both EMAs in one bar.
        """
        n = 80
        idx = pd.date_range("2024-01-02", periods=n, freq="B", tz="UTC")
        prices = np.full(n, 450.0)
        # Breakout starts at bar 40 — prices rise sharply
        for i in range(40, n):
            prices[i] = 450.0 + (i - 39) * 3.0   # +3 per bar, clear uptrend

        # Compression candles: tiny range
        high = np.where(np.arange(n) < 40, prices + 0.1, prices + 2.0)
        low  = np.where(np.arange(n) < 40, prices - 0.1, prices - 0.0)

        df = pd.DataFrame(
            {
                "open":   prices - 0.05,
                "high":   high,
                "low":    low,
                "close":  prices,
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
