"""
Tests for the ORB strategy using the intraday 5-minute signal path.

These tests cover the production data path:
  - UTC-indexed bars from yfinance (13:30 UTC = 9:30 ET)
  - Multi-day datasets (one signal per day)
  - Late-start sessions (first bar after 9:45 → skip)
  - Volume confirmation gate
  - Signal timestamp accuracy (matches the actual breakout bar)

The existing TestOpeningRangeBreakout in test_strategies.py covers the
strategy logic in isolation.  This file covers the intraday time-of-day
path that the live paper loop uses.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from app.strategies import OpeningRangeBreakoutStrategy
from app.strategies.strategy_base import SignalDirection

ET = ZoneInfo("America/New_York")


def _make_5min_bars(
    date_str: str = "2024-01-02",
    direction: str = "up",
    n: int = 40,
    start_hour_utc: int = 14,   # 14:30 UTC = 9:30 ET
    start_min_utc: int = 30,
    base_price: float = 450.0,
    or_bars: int = 3,            # number of bars in the opening range
    breakout_bar_idx: int = 4,   # index of the breakout bar (post-range)
) -> pd.DataFrame:
    """
    Build a synthetic 5-minute OHLCV DataFrame.

    Opening range spans `or_bars` five-minute bars (default = 3 → 15 min).
    The breakout candle is at `breakout_bar_idx` with 4× the average volume.
    Bars are indexed in UTC (as yfinance returns them).
    """
    start = f"{date_str} {start_hour_utc:02d}:{start_min_utc:02d}"
    idx = pd.date_range(start, periods=n, freq="5min", tz="UTC")

    closes = np.full(n, base_price)
    volumes = np.full(n, 1_000_000, dtype=float)

    # Opening range: tight consolidation (range < 1 pt)
    for i in range(or_bars):
        closes[i] = base_price + np.random.uniform(-0.15, 0.15)

    # Breakout bar
    if direction == "up":
        closes[breakout_bar_idx] = base_price + 3.0
    else:
        closes[breakout_bar_idx] = base_price - 3.0
    volumes[breakout_bar_idx] = 4_000_000

    df = pd.DataFrame(
        {
            "open":   closes - 0.1,
            "high":   closes + 0.5,
            "low":    closes - 0.5,
            "close":  closes,
            "volume": volumes,
        },
        index=idx,
    )
    return df


# ── Core intraday path ─────────────────────────────────────────────────────────

class TestIntradayORBSignalPath:

    def _strat(self, **kwargs):
        defaults = {"range_minutes": 15, "min_range_pts": 0.1, "volume_confirmation": True}
        defaults.update(kwargs)
        return OpeningRangeBreakoutStrategy(params=defaults)

    def test_utc_bars_5min_upside_long_signal(self):
        """UTC 5-min bars starting at 9:30 ET produce a LONG signal on upside breakout."""
        bars = _make_5min_bars(direction="up")
        signals = self._strat().generate_signals(bars, "SPY")
        long_sigs = [s for s in signals if s.direction == SignalDirection.LONG]
        assert len(long_sigs) >= 1, "Expected at least one LONG signal from upside breakout"

    def test_utc_bars_5min_downside_short_signal(self):
        """UTC 5-min bars starting at 9:30 ET produce a SHORT signal on downside breakout."""
        bars = _make_5min_bars(direction="down")
        signals = self._strat().generate_signals(bars, "SPY")
        short_sigs = [s for s in signals if s.direction == SignalDirection.SHORT]
        assert len(short_sigs) >= 1

    def test_multi_day_one_signal_per_day(self):
        """Two days of 5-min bars, each with a breakout, yield two independent signals."""
        day1 = _make_5min_bars("2024-01-02", "up")
        day2 = _make_5min_bars("2024-01-03", "up")
        bars = pd.concat([day1, day2])
        signals = self._strat().generate_signals(bars, "SPY")
        long_sigs = [s for s in signals if s.direction == SignalDirection.LONG]
        assert len(long_sigs) == 2, (
            f"Expected 2 LONG signals (one per day), got {len(long_sigs)}"
        )
        # Signals should be on different dates
        dates = {s.timestamp.date() for s in long_sigs}
        assert len(dates) == 2

    def test_late_start_session_skipped(self):
        """
        First bar at 10:00 ET (15:00 UTC) — after the 9:45 guard — should produce
        no signal because the opening range cannot be established.
        """
        # 15:00 UTC = 10:00 ET
        bars = _make_5min_bars(start_hour_utc=15, start_min_utc=0, direction="up")
        signals = self._strat().generate_signals(bars, "SPY")
        assert signals == [], (
            f"Expected no signals when session starts at 10:00 ET, got {len(signals)}"
        )

    def test_bars_starting_at_9_50_et_skipped(self):
        """
        First bar at 9:50 ET (14:50 UTC) — strictly after the 9:45 guard — should
        produce no signal.  The strategy guard is `first_bar_time > time(9, 45)`.
        """
        # 14:50 UTC = 9:50 ET
        bars = _make_5min_bars(start_hour_utc=14, start_min_utc=50, direction="up")
        signals = self._strat().generate_signals(bars, "SPY")
        assert signals == [], (
            f"Expected no signals when session starts at 9:50 ET, got {len(signals)}"
        )

    def test_volume_confirmation_blocks_equal_volume_breakout(self):
        """
        When all bars have the same volume, breakout vol == avg vol (not strictly >),
        so volume_confirmation=True should block the signal.
        """
        bars = _make_5min_bars(direction="up")
        bars["volume"] = 1_000_000  # uniform — breakout bar no longer stands out
        signals = self._strat(volume_confirmation=True).generate_signals(bars, "SPY")
        assert signals == [], "Uniform volume should block volume-confirmation gate"

    def test_volume_confirmation_disabled_allows_any_volume(self):
        """Same bars pass when volume_confirmation=False."""
        bars = _make_5min_bars(direction="up")
        bars["volume"] = 1_000_000
        signals = self._strat(volume_confirmation=False).generate_signals(bars, "SPY")
        long_sigs = [s for s in signals if s.direction == SignalDirection.LONG]
        assert len(long_sigs) >= 1

    def test_signal_timestamp_is_breakout_bar(self):
        """
        Signal.timestamp must equal the exact timestamp of the breakout candle,
        not the opening-range end or any other bar.
        """
        # Breakout bar is at index 4 → 14:30 + 4×5min = 14:50 UTC = 9:50 ET
        bars = _make_5min_bars("2024-01-02", "up", breakout_bar_idx=4)
        signals = self._strat().generate_signals(bars, "SPY")
        long_sigs = [s for s in signals if s.direction == SignalDirection.LONG]
        assert long_sigs, "No LONG signal generated"

        sig = long_sigs[0]
        expected_utc = pd.Timestamp("2024-01-02 14:50", tz="UTC")
        expected_et = expected_utc.tz_convert("America/New_York").to_pydatetime()
        assert sig.timestamp == expected_et, (
            f"Expected timestamp {expected_et}, got {sig.timestamp}"
        )

    def test_signal_metadata_includes_range(self):
        """Signal metadata must carry or_high, or_low, or_range from the opening range."""
        bars = _make_5min_bars("2024-01-02", "up")
        signals = self._strat().generate_signals(bars, "SPY")
        long_sigs = [s for s in signals if s.direction == SignalDirection.LONG]
        assert long_sigs
        meta = long_sigs[0].metadata
        assert "or_high" in meta
        assert "or_low" in meta
        assert "or_range" in meta
        assert meta["or_range"] > 0

    def test_narrow_range_threshold_respected(self):
        """
        A very large min_range_pts threshold should filter out the tight synthetic
        opening range and produce no signal.
        """
        bars = _make_5min_bars("2024-01-02", "up")
        signals = self._strat(min_range_pts=100.0).generate_signals(bars, "SPY")
        assert signals == []

    def test_one_signal_per_day_only(self):
        """
        Even if there are multiple breakout bars, ORB fires at most once per
        direction per day (breaks after first match).
        """
        bars = _make_5min_bars("2024-01-02", "up", n=78)
        # Force two post-range bars to be well above or_high
        bars.iloc[4, bars.columns.get_loc("close")] = 453.0
        bars.iloc[6, bars.columns.get_loc("close")] = 455.0
        signals = self._strat().generate_signals(bars, "SPY")
        long_sigs = [s for s in signals if s.direction == SignalDirection.LONG]
        assert len(long_sigs) == 1, "ORB should fire at most once per day per direction"

    def test_lowercase_columns_accepted(self):
        """Strategy must work with lowercase column names (yfinance default after normalisation)."""
        bars = _make_5min_bars("2024-01-02", "up")
        # Columns are already lowercase in _make_5min_bars; just verify no error
        signals = self._strat().generate_signals(bars, "SPY")
        assert isinstance(signals, list)

    def test_mixed_case_columns_accepted(self):
        """Strategy normalises mixed-case column names internally."""
        bars = _make_5min_bars("2024-01-02", "up")
        bars.columns = ["Open", "High", "Low", "Close", "Volume"]
        signals = self._strat().generate_signals(bars, "SPY")
        assert isinstance(signals, list)
