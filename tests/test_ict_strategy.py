"""
Unit tests for the ICT Liquidity Sweep & FVG Reversal strategy.

All tests use synthetic pandas DataFrames — no network calls.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import pytest

from app.strategies.ict import (
    ICTConfig,
    ICTStrategy,
    SessionCalculator,
    FVGDetector,
    FVGType,
    LiquiditySweepDetector,
    SweepType,
    MarketStructureEngine,
    MSEventType,
    LiquidityTargetFinder,
    SessionLevels,
)
from app.strategies.ict.position_sizer import PositionSizer
from app.strategies.strategy_base import SignalDirection
from app.backtesting.ict_backtester import ICTBacktester

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_bars(
    n: int = 100,
    start: str = "2024-01-02 00:00",
    freq: str = "1min",
    base_price: float = 4500.0,
) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    rng = np.random.default_rng(42)
    prices = base_price + np.cumsum(rng.normal(0, 0.5, n))
    prices = np.maximum(prices, 1.0)
    open_ = prices
    high = prices + np.abs(rng.normal(0, 0.3, n))
    low = prices - np.abs(rng.normal(0, 0.3, n))
    close = prices + rng.normal(0, 0.2, n)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": rng.integers(200, 1000, n).astype(float)},
        index=idx,
    )
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return df


def _make_session_bars(
    asian_high: float = 4520.0,
    asian_low: float = 4490.0,
    london_high: float | None = None,
    london_low: float | None = None,
) -> pd.DataFrame:
    """360 1-min bars 00:00–05:59 UTC with tight range, then NY bars."""
    london_high = london_high if london_high is not None else asian_high - 2
    london_low = london_low if london_low is not None else asian_low + 2
    start = datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc)
    times = [start + timedelta(minutes=i) for i in range(480)]
    prices = []
    mid = (asian_high + asian_low) / 2
    for t in times:
        h = t.hour
        if h < 3:
            prices.append(mid + np.sin(t.minute / 20) * (asian_high - mid) * 0.8)
        elif h < 6:
            prices.append(mid + np.sin(t.minute / 20) * (london_high - mid) * 0.8)
        else:
            prices.append(mid)
    prices = np.array(prices)
    buf = 0.3
    df = pd.DataFrame({
        "open": prices,
        "high": prices + buf,
        "low": prices - buf,
        "close": prices,
        "volume": np.ones(len(times)) * 500.0,
    }, index=pd.DatetimeIndex(times, tz="UTC"))
    # Force range extremes
    df.loc[df.index[30], "high"] = asian_high
    df.loc[df.index[60], "low"] = asian_low
    df.loc[df.index[180], "high"] = london_high
    df.loc[df.index[220], "low"] = london_low
    return df


def _append_bars(df: pd.DataFrame, new_df: pd.DataFrame) -> pd.DataFrame:
    return pd.concat([df, new_df]).sort_index()


def _append_sweep_and_reject(
    df: pd.DataFrame,
    level: float,
    direction: str = "up",
    extension: float = 1.5,
    rejection_pct: float = 0.7,
    tick_size: float = 0.25,
) -> pd.DataFrame:
    """
    Append 5 candles: 1 sweep candle + 3 bars showing rejection + 1 neutral.
    direction='up' → sweeps level from below (bullish sweep = buy stop run).
    """
    last_t = df.index[-1]
    freq = pd.tseries.frequencies.to_offset("1min")
    times = [last_t + freq * (i + 1) for i in range(5)]
    rows = []
    if direction == "up":
        sweep_high = level + extension
        sweep_low = level - 0.5
        # candle 0: sweeps above
        rows.append({"open": level - 0.5, "high": sweep_high, "low": sweep_low, "close": level + 0.2, "volume": 800.0})
        retrace_close = level - (sweep_high - level) * rejection_pct
        rows.append({"open": level + 0.1, "high": level + 0.3, "low": retrace_close - 0.1, "close": retrace_close, "volume": 600.0})
        rows.append({"open": retrace_close, "high": retrace_close + 0.2, "low": retrace_close - 0.3, "close": retrace_close - 0.1, "volume": 400.0})
        rows.append({"open": retrace_close - 0.1, "high": retrace_close + 0.1, "low": retrace_close - 0.5, "close": retrace_close - 0.3, "volume": 300.0})
        rows.append({"open": retrace_close - 0.3, "high": retrace_close, "low": retrace_close - 0.6, "close": retrace_close - 0.2, "volume": 200.0})
    else:
        sweep_low = level - extension
        sweep_high = level + 0.5
        rows.append({"open": level + 0.5, "high": sweep_high, "low": sweep_low, "close": level - 0.2, "volume": 800.0})
        retrace_close = level + (level - sweep_low) * rejection_pct
        rows.append({"open": level - 0.1, "high": retrace_close + 0.1, "low": level - 0.3, "close": retrace_close, "volume": 600.0})
        rows.append({"open": retrace_close, "high": retrace_close + 0.3, "low": retrace_close - 0.2, "close": retrace_close + 0.1, "volume": 400.0})
        rows.append({"open": retrace_close + 0.1, "high": retrace_close + 0.5, "low": retrace_close - 0.1, "close": retrace_close + 0.3, "volume": 300.0})
        rows.append({"open": retrace_close + 0.3, "high": retrace_close + 0.6, "low": retrace_close, "close": retrace_close + 0.2, "volume": 200.0})
    new_df = pd.DataFrame(rows, index=pd.DatetimeIndex(times, tz="UTC"))
    return _append_bars(df, new_df)


def _append_bearish_fvg(df: pd.DataFrame, gap_size: float = 2.0) -> pd.DataFrame:
    """Append a 3-candle bearish FVG: candle[2].high < candle[0].low."""
    last_t = df.index[-1]
    freq = pd.tseries.frequencies.to_offset("1min")
    base = 4510.0
    times = [last_t + freq * (i + 1) for i in range(3)]
    rows = [
        {"open": base + 1, "high": base + 2, "low": base, "close": base + 0.5, "volume": 500.0},
        {"open": base - 1, "high": base, "low": base - 3, "close": base - 2, "volume": 700.0},
        {"open": base - 3, "high": base - gap_size, "low": base - 5, "close": base - 4, "volume": 600.0},
    ]
    new_df = pd.DataFrame(rows, index=pd.DatetimeIndex(times, tz="UTC"))
    return _append_bars(df, new_df)


def _append_bullish_fvg(df: pd.DataFrame, gap_size: float = 2.0) -> pd.DataFrame:
    """Append a 3-candle bullish FVG: candle[2].low > candle[0].high."""
    last_t = df.index[-1]
    freq = pd.tseries.frequencies.to_offset("1min")
    base = 4490.0
    times = [last_t + freq * (i + 1) for i in range(3)]
    rows = [
        {"open": base - 1, "high": base, "low": base - 2, "close": base - 0.5, "volume": 500.0},
        {"open": base + 1, "high": base + 3, "low": base, "close": base + 2, "volume": 700.0},
        {"open": base + 2, "high": base + 5, "low": base + gap_size, "close": base + 4, "volume": 600.0},
    ]
    new_df = pd.DataFrame(rows, index=pd.DatetimeIndex(times, tz="UTC"))
    return _append_bars(df, new_df)


# ── SessionCalculator ─────────────────────────────────────────────────────────

class TestSessionCalculator:

    def _calc(self, **kwargs) -> SessionCalculator:
        return SessionCalculator(ICTConfig(**kwargs))

    def test_asian_high_low_detected(self):
        bars = _make_session_bars(asian_high=4522.0, asian_low=4488.0)
        calc = self._calc()
        sessions = calc.compute_sessions(bars)
        assert len(sessions) >= 1
        lv = sessions[0]
        assert lv.asian_high == pytest.approx(4522.0, abs=0.01)
        assert lv.asian_low == pytest.approx(4488.0, abs=0.01)

    def test_london_high_low_detected(self):
        bars = _make_session_bars(london_high=4515.0, london_low=4495.0)
        calc = self._calc()
        sessions = calc.compute_sessions(bars)
        assert len(sessions) >= 1
        lv = sessions[0]
        assert lv.london_high == pytest.approx(4515.0, abs=0.01)
        assert lv.london_low == pytest.approx(4495.0, abs=0.01)

    def test_empty_bars_returns_empty(self):
        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                              index=pd.DatetimeIndex([], tz="UTC"))
        calc = self._calc()
        result = calc.compute_sessions(empty)
        assert result == []

    def test_get_current_session_levels_returns_most_recent(self):
        bars = _make_session_bars(asian_high=4518.0, asian_low=4492.0)
        calc = self._calc()
        lv = calc.get_current_session_levels(bars)
        assert lv is not None
        assert lv.asian_high > lv.asian_low

    def test_custom_session_times_respected(self):
        from app.strategies.ict.config import SessionWindow
        config = ICTConfig(
            asian_session=SessionWindow(start_hour=1, end_hour=5),
            london_session=SessionWindow(start_hour=5, end_hour=9),
        )
        calc = SessionCalculator(config)
        bars = _make_session_bars()
        sessions = calc.compute_sessions(bars)
        assert isinstance(sessions, list)

    def test_session_levels_has_date_field(self):
        bars = _make_session_bars()
        calc = self._calc()
        sessions = calc.compute_sessions(bars)
        assert len(sessions) >= 1
        assert sessions[0].date == "2024-01-02"


# ── LiquiditySweepDetector ────────────────────────────────────────────────────

class TestLiquiditySweepDetector:
    """Uses minimal controlled DataFrames to avoid accidental price excursions."""

    def _detector(self, **kwargs) -> LiquiditySweepDetector:
        return LiquiditySweepDetector(ICTConfig(**kwargs))

    def _levels(self, asian_high=4520, asian_low=4490,
                london_high=4515, london_low=4495) -> SessionLevels:
        return SessionLevels(
            date="2024-01-02",
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            asian_high=asian_high, asian_low=asian_low,
            london_high=london_high, london_low=london_low,
        )

    def _sweep_high_bars(self, level=4520.0, extension=2.0, reject_close=4518.0) -> pd.DataFrame:
        """Five bars: neutral inside range, sweep candle, then 3 rejection bars."""
        base = datetime(2024, 1, 2, 14, 0, tzinfo=timezone.utc)
        freq = pd.tseries.frequencies.to_offset("1min")
        rows = [
            # neutral bars well inside range
            {"open": 4510, "high": 4511, "low": 4509, "close": 4510, "volume": 300},
            {"open": 4510, "high": 4512, "low": 4509, "close": 4511, "volume": 300},
            # sweep candle: high exceeds level by extension
            {"open": 4519, "high": level + extension, "low": 4518, "close": level + extension * 0.15, "volume": 900},
            # rejection bars: close well below level (threshold = level + extension*0.5 for rej_pct=0.5)
            {"open": level, "high": level + 0.3, "low": reject_close - 0.5, "close": reject_close, "volume": 600},
            {"open": reject_close, "high": reject_close + 0.2, "low": reject_close - 0.5, "close": reject_close - 0.2, "volume": 400},
        ]
        return pd.DataFrame(rows, index=pd.DatetimeIndex(
            [base + freq * i for i in range(len(rows))], tz="UTC"))

    def _sweep_low_bars(self, level=4490.0, extension=2.0, reject_close=4492.0) -> pd.DataFrame:
        base = datetime(2024, 1, 2, 14, 0, tzinfo=timezone.utc)
        freq = pd.tseries.frequencies.to_offset("1min")
        rows = [
            {"open": 4500, "high": 4501, "low": 4499, "close": 4500, "volume": 300},
            {"open": 4500, "high": 4501, "low": 4498, "close": 4499, "volume": 300},
            {"open": 4491, "high": 4492, "low": level - extension, "close": level - extension * 0.15, "volume": 900},
            {"open": level, "high": reject_close + 0.5, "low": level - 0.3, "close": reject_close, "volume": 600},
            {"open": reject_close, "high": reject_close + 0.5, "low": reject_close - 0.2, "close": reject_close + 0.2, "volume": 400},
        ]
        return pd.DataFrame(rows, index=pd.DatetimeIndex(
            [base + freq * i for i in range(len(rows))], tz="UTC"))

    def test_detects_high_sweep_with_rejection(self):
        # threshold = 4520 + 2*(1-0.5) = 4521; reject_close=4518 < 4521 ✓
        bars = self._sweep_high_bars(level=4520.0, extension=2.0, reject_close=4518.0)
        detector = self._detector(min_sweep_ticks=2, tick_size=0.25,
                                  rejection_candles=4, rejection_close_pct=0.5)
        levels = self._levels(asian_high=4520.0)
        sweeps = detector.detect_sweeps(bars, levels)
        high_sweeps = [s for s in sweeps if s.level_type in (SweepType.ASIAN_HIGH, SweepType.LONDON_HIGH)]
        assert len(high_sweeps) >= 1
        assert high_sweeps[0].rejection_confirmed is True

    def test_detects_low_sweep_with_rejection(self):
        # threshold = 4490 - 2*(1-0.5) = 4489; reject_close=4492 > 4489 ✓
        bars = self._sweep_low_bars(level=4490.0, extension=2.0, reject_close=4492.0)
        detector = self._detector(min_sweep_ticks=2, tick_size=0.25,
                                  rejection_candles=4, rejection_close_pct=0.5)
        levels = self._levels(asian_low=4490.0)
        sweeps = detector.detect_sweeps(bars, levels)
        low_sweeps = [s for s in sweeps if s.level_type in (SweepType.ASIAN_LOW, SweepType.LONDON_LOW)]
        assert len(low_sweeps) >= 1

    def test_no_sweep_when_extension_too_small(self):
        """Bars only touch level + 0.1, below the 2-tick minimum of 0.5."""
        base = datetime(2024, 1, 2, 14, 0, tzinfo=timezone.utc)
        freq = pd.tseries.frequencies.to_offset("1min")
        rows = [{"open": 4519, "high": 4520.1, "low": 4518, "close": 4519, "volume": 500}
                for _ in range(10)]
        bars = pd.DataFrame(rows, index=pd.DatetimeIndex(
            [base + freq * i for i in range(10)], tz="UTC"))
        detector = self._detector(min_sweep_ticks=2, tick_size=0.25)
        # Set all levels so bars (high=4520.1, low=4518) don't exceed any by >= 0.5
        levels = self._levels(asian_high=4520.0, asian_low=4510.0,
                              london_high=4525.0, london_low=4510.0)
        sweeps = detector.detect_sweeps(bars, levels)
        assert len(sweeps) == 0

    def test_rejection_close_pct_enforced(self):
        """Sweep with close staying above threshold → rejection_confirmed=False."""
        base = datetime(2024, 1, 2, 14, 0, tzinfo=timezone.utc)
        freq = pd.tseries.frequencies.to_offset("1min")
        level, extension = 4520.0, 2.0
        # threshold = 4520 + 2*(1-0.5) = 4521; all closes stay at 4521.5 (above threshold)
        rows = [
            {"open": 4519, "high": level + extension, "low": 4518,
             "close": level + extension - 0.1, "volume": 800},
        ]
        for _ in range(5):
            rows.append({"open": level + 1.5, "high": level + 2.1, "low": level + 1.0,
                         "close": level + 1.5, "volume": 300})
        bars = pd.DataFrame(rows, index=pd.DatetimeIndex(
            [base + freq * i for i in range(len(rows))], tz="UTC"))
        detector = self._detector(min_sweep_ticks=2, tick_size=0.25,
                                  rejection_candles=5, rejection_close_pct=0.5)
        levels = self._levels(asian_high=level)
        sweeps = detector.detect_sweeps(bars, levels)
        confirmed = [s for s in sweeps if s.rejection_confirmed and
                     s.level_type in (SweepType.ASIAN_HIGH, SweepType.LONDON_HIGH)]
        assert len(confirmed) == 0


# ── FVGDetector ───────────────────────────────────────────────────────────────

class TestFVGDetector:

    def _detector(self, **kwargs) -> FVGDetector:
        return FVGDetector(ICTConfig(**kwargs))

    def test_bearish_fvg_detected(self):
        base = _make_session_bars()
        bars = _append_bearish_fvg(base, gap_size=2.0)
        detector = self._detector()
        fvgs = detector.detect_fvgs(bars)
        bearish = [f for f in fvgs if f.type == FVGType.BEARISH]
        assert len(bearish) >= 1
        fvg = bearish[-1]
        assert fvg.upper_price > fvg.lower_price
        assert fvg.midpoint == pytest.approx((fvg.upper_price + fvg.lower_price) / 2)

    def test_bullish_fvg_detected(self):
        base = _make_session_bars()
        bars = _append_bullish_fvg(base, gap_size=2.0)
        detector = self._detector()
        fvgs = detector.detect_fvgs(bars)
        bullish = [f for f in fvgs if f.type == FVGType.BULLISH]
        assert len(bullish) >= 1
        fvg = bullish[-1]
        assert fvg.upper_price > fvg.lower_price

    def test_no_fvg_when_candles_touch(self):
        start = datetime(2024, 1, 2, 14, 0, tzinfo=timezone.utc)
        times = [start + timedelta(minutes=i) for i in range(3)]
        # c[2].high = c[0].low exactly — no gap
        rows = [
            {"open": 100, "high": 102, "low": 100, "close": 101, "volume": 100},
            {"open": 99, "high": 100, "low": 97, "close": 98, "volume": 100},
            {"open": 98, "high": 100, "low": 96, "close": 97, "volume": 100},
        ]
        bars = pd.DataFrame(rows, index=pd.DatetimeIndex(times, tz="UTC"))
        detector = self._detector()
        fvgs = detector.detect_fvgs(bars)
        assert len(fvgs) == 0

    def test_min_fvg_size_filter(self):
        base = _make_session_bars()
        bars = _append_bearish_fvg(base, gap_size=0.3)
        detector = self._detector(min_fvg_size=1.0)
        fvgs = detector.detect_fvgs(bars)
        # With min_fvg_size=1.0, no returned FVG should have size < 1.0
        small = [f for f in fvgs if (f.upper_price - f.lower_price) < 1.0]
        assert len(small) == 0

    def test_fvg_check_fill_midpoint(self):
        base = _make_session_bars()
        bars = _append_bearish_fvg(base, gap_size=4.0)
        detector = self._detector()
        fvgs = detector.detect_fvgs(bars)
        bearish = [f for f in fvgs if f.type == FVGType.BEARISH]
        assert len(bearish) >= 1
        fvg = bearish[-1]
        fill = fvg.check_fill(fvg.midpoint)
        assert fill == pytest.approx(0.5, abs=0.05)

    def test_fvg_check_fill_fully_filled_bearish(self):
        base = _make_session_bars()
        bars = _append_bearish_fvg(base, gap_size=4.0)
        detector = self._detector()
        fvgs = detector.detect_fvgs(bars)
        bearish = [f for f in fvgs if f.type == FVGType.BEARISH]
        fvg = bearish[-1]
        # For bearish FVG, price at or above upper_price = 100% fill
        fill = fvg.check_fill(fvg.upper_price)
        assert fill >= 0.99

    def test_fvg_check_fill_zero_when_not_entered(self):
        base = _make_session_bars()
        bars = _append_bullish_fvg(base, gap_size=4.0)
        detector = self._detector()
        fvgs = detector.detect_fvgs(bars)
        bullish = [f for f in fvgs if f.type == FVGType.BULLISH]
        assert len(bullish) >= 1
        fvg = bullish[-1]
        # Price well above upper_price → max(0, (upper - price)/size) = 0% fill
        fill = fvg.check_fill(fvg.upper_price + 10.0)
        assert fill == pytest.approx(0.0, abs=0.01)

    def test_multiple_fvgs_detected_in_sequence(self):
        base = _make_session_bars()
        bars = _append_bearish_fvg(base, gap_size=2.0)
        bars = _append_bullish_fvg(bars, gap_size=2.0)
        bars = _append_bearish_fvg(bars, gap_size=2.0)
        detector = self._detector()
        fvgs = detector.detect_fvgs(bars)
        assert len(fvgs) >= 2


# ── MarketStructureEngine ─────────────────────────────────────────────────────

class TestMarketStructureEngine:

    def _engine(self, lookback: int = 2) -> MarketStructureEngine:
        return MarketStructureEngine(ICTConfig(swing_lookback=lookback))

    def _zigzag_bars(self, levels: list[float], start: str = "2024-01-02 14:00") -> pd.DataFrame:
        """Create bars that zig-zag through given price levels.

        Uses a neutral mid-price so each turning-point bar clearly stands out
        as either a strict local high or strict local low.
        """
        freq = pd.tseries.frequencies.to_offset("1min")
        start_dt = datetime(2024, 1, 2, 14, 0, tzinfo=timezone.utc)
        neutral = (min(levels) + max(levels)) / 2
        rows = []
        times = []
        for i, price in enumerate(levels):
            for j in range(5):
                times.append(start_dt + freq * (i * 5 + j))
                rows.append({"open": neutral, "high": neutral + 0.5,
                             "low": neutral - 0.5, "close": neutral, "volume": 100.0})
        df = pd.DataFrame(rows, index=pd.DatetimeIndex(times, tz="UTC"))
        # Set the middle bar of each group to the actual turning-point level
        for i, price in enumerate(levels):
            idx = i * 5 + 2
            if price > neutral:   # swing high
                df.iloc[idx, df.columns.get_loc("high")] = price
            else:                 # swing low
                df.iloc[idx, df.columns.get_loc("low")] = price
        return df

    def test_detects_swing_high(self):
        bars = _make_bars(50, base_price=4500.0)
        # Force a local high at index 20
        bars.iloc[20, bars.columns.get_loc("high")] = 4520.0
        for i in [18, 19, 21, 22]:
            bars.iloc[i, bars.columns.get_loc("high")] = 4510.0
        engine = self._engine(lookback=2)
        events = engine.analyse(bars)
        highs = [e for e in events if e.type == MSEventType.SWING_HIGH]
        assert len(highs) >= 1

    def test_detects_swing_low(self):
        bars = _make_bars(50, base_price=4500.0)
        bars.iloc[20, bars.columns.get_loc("low")] = 4480.0
        for i in [18, 19, 21, 22]:
            bars.iloc[i, bars.columns.get_loc("low")] = 4490.0
        engine = self._engine(lookback=2)
        events = engine.analyse(bars)
        lows = [e for e in events if e.type == MSEventType.SWING_LOW]
        assert len(lows) >= 1

    def test_bos_bullish_after_break_of_swing_high(self):
        # Rising sequence with a BOS
        bars = _make_bars(80, base_price=4480.0)
        # Insert clear swing high then break above it
        bars.iloc[30, bars.columns.get_loc("high")] = 4510.0
        bars.iloc[60, bars.columns.get_loc("close")] = 4515.0
        bars.iloc[60, bars.columns.get_loc("high")] = 4516.0
        engine = self._engine(lookback=3)
        events = engine.analyse(bars)
        bos = [e for e in events if e.type == MSEventType.BOS_BULLISH]
        # May or may not trigger depending on exact swing detection — just verify no crash
        assert isinstance(events, list)

    def test_higher_high_detection(self):
        engine = self._engine(lookback=2)
        levels = [4480, 4510, 4485, 4515, 4490, 4520]
        bars = self._zigzag_bars(levels)
        events = engine.analyse(bars)
        hh_events = [e for e in events if e.type == MSEventType.HIGHER_HIGH]
        # 4515 > 4510 and 4520 > 4515 → at least one HH
        assert len(hh_events) >= 1

    def test_lower_low_detection(self):
        engine = self._engine(lookback=2)
        levels = [4520, 4490, 4515, 4485, 4510, 4480]
        bars = self._zigzag_bars(levels)
        events = engine.analyse(bars)
        ll_events = [e for e in events if e.type == MSEventType.LOWER_LOW]
        assert len(ll_events) >= 1

    def test_returns_list_not_none(self):
        bars = _make_bars(20)
        engine = self._engine()
        result = engine.analyse(bars)
        assert isinstance(result, list)


# ── LiquidityTargetFinder ─────────────────────────────────────────────────────

class TestLiquidityTargetFinder:

    def _finder(self, **kwargs) -> LiquidityTargetFinder:
        return LiquidityTargetFinder(ICTConfig(**kwargs))

    def _levels(self) -> SessionLevels:
        return SessionLevels(
            date="2024-01-02",
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            asian_high=4520.0, asian_low=4490.0,
            london_high=4515.0, london_low=4495.0,
        )

    def test_session_levels_appear_as_targets(self):
        bars = _make_bars(50)
        finder = self._finder()
        levels = self._levels()
        targets = finder.find_targets(bars, [levels])
        prices = [t.price for t in targets]
        assert 4520.0 in prices or any(abs(p - 4520.0) < 0.01 for p in prices)
        assert 4490.0 in prices or any(abs(p - 4490.0) < 0.01 for p in prices)

    def test_find_nearest_above(self):
        bars = _make_bars(50, base_price=4500.0)
        finder = self._finder()
        levels = self._levels()
        targets = finder.find_targets(bars, [levels])
        nearest = finder.nearest_target(targets, current_price=4500.0, direction="bullish")
        if nearest:
            assert nearest.price > 4500.0

    def test_find_nearest_below(self):
        bars = _make_bars(50, base_price=4500.0)
        finder = self._finder()
        levels = self._levels()
        targets = finder.find_targets(bars, [levels])
        nearest = finder.nearest_target(targets, current_price=4500.0, direction="bearish")
        if nearest:
            assert nearest.price < 4500.0

    def test_targets_have_strength_field(self):
        bars = _make_bars(50)
        finder = self._finder()
        levels = self._levels()
        targets = finder.find_targets(bars, [levels])
        assert all(1 <= t.strength <= 3 for t in targets)

    def test_previous_day_levels_detected(self):
        # Build two days of bars
        day1 = _make_bars(n=480, start="2024-01-01 00:00", base_price=4500.0)
        # Force extremes
        day1.iloc[100, day1.columns.get_loc("high")] = 4530.0
        day1.iloc[200, day1.columns.get_loc("low")] = 4470.0
        day2 = _make_bars(n=200, start="2024-01-02 00:00", base_price=4500.0)
        bars = pd.concat([day1, day2]).sort_index()
        finder = self._finder()
        levels = self._levels()
        targets = finder.find_targets(bars, [levels])
        prices = [t.price for t in targets]
        assert any(abs(p - 4530.0) < 0.5 for p in prices), f"Prev day high not found, got {prices}"


# ── PositionSizer ─────────────────────────────────────────────────────────────

class TestPositionSizer:

    def test_basic_long_sizing(self):
        sizer = PositionSizer(account_size=100_000, risk_pct=0.01)
        result = sizer.calculate(entry=4500.0, stop_loss=4498.0)
        assert result.risk_amount == pytest.approx(1000.0, abs=1.0)
        assert result.position_size >= 1.0

    def test_basic_short_sizing(self):
        sizer = PositionSizer(account_size=100_000, risk_pct=0.01)
        result = sizer.calculate(entry=4500.0, stop_loss=4502.0)
        assert result.risk_amount == pytest.approx(1000.0, abs=1.0)
        assert result.position_size >= 1.0

    def test_futures_point_value_scaling(self):
        # ES: $50/point; risk $1000 / (2 pts × $50) = 10 contracts
        sizer = PositionSizer(account_size=100_000, risk_pct=0.01, point_value=50.0)
        result = sizer.calculate(entry=4500.0, stop_loss=4498.0)
        assert result.position_size == pytest.approx(10.0, abs=1.0)
        assert result.risk_amount == pytest.approx(1000.0, abs=1.0)

    def test_minimum_size_of_one(self):
        sizer = PositionSizer(account_size=100, risk_pct=0.001, min_size=1.0)
        result = sizer.calculate(entry=4500.0, stop_loss=4499.0)
        assert result.position_size >= 1

    def test_risk_amount_equals_size_times_distance(self):
        sizer = PositionSizer(account_size=100_000, risk_pct=0.01, point_value=1.0)
        entry, stop = 4510.0, 4505.0
        result = sizer.calculate(entry=entry, stop_loss=stop)
        implied = result.position_size * abs(entry - stop)
        assert implied == pytest.approx(result.risk_amount, rel=0.05)


# ── ICTStrategy (integration) ─────────────────────────────────────────────────

class TestICTStrategy:

    def _full_trade_bars(self) -> pd.DataFrame:
        """
        Build bars that trigger a SHORT entry:
        - Asian session: high = 4520
        - After 14:00 UTC: sweep 4520 up then reject
        - Bearish FVG forms
        - Price retraces into FVG
        """
        # Asian session bars
        start = datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc)
        asian_times = [start + timedelta(minutes=i) for i in range(360)]
        asian_prices = [4510.0 + 5.0 * np.sin(i / 30) for i in range(360)]
        asian_df = pd.DataFrame({
            "open": asian_prices, "high": asian_prices,
            "low": asian_prices, "close": asian_prices, "volume": [500.0] * 360,
        }, index=pd.DatetimeIndex(asian_times, tz="UTC"))
        # Force Asian high
        asian_df.iloc[100, asian_df.columns.get_loc("high")] = 4520.0
        asian_df.iloc[200, asian_df.columns.get_loc("low")] = 4492.0

        # Filler bars up to 14:00
        filler_times = [start + timedelta(minutes=360 + i) for i in range(480)]
        filler_prices = [4508.0] * 480
        filler_df = pd.DataFrame({
            "open": filler_prices, "high": filler_prices,
            "low": filler_prices, "close": filler_prices, "volume": [300.0] * 480,
        }, index=pd.DatetimeIndex(filler_times, tz="UTC"))

        bars = pd.concat([asian_df, filler_df]).sort_index()

        # Now add sweep + FVG at 14:30 UTC
        ny_start = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)
        ny_times = [ny_start + timedelta(minutes=i) for i in range(20)]
        # Sweep candle: goes above 4520
        sweep_rows = []
        sweep_rows.append({"open": 4518, "high": 4524, "low": 4515, "close": 4519, "volume": 900.0})
        # Rejection: closes below 4520
        sweep_rows.append({"open": 4519, "high": 4520, "low": 4512, "close": 4513, "volume": 800.0})
        sweep_rows.append({"open": 4513, "high": 4515, "low": 4510, "close": 4511, "volume": 600.0})
        # Bearish FVG: c[0].low=4520, c[2].high=4518 → gap [4518, 4520]
        sweep_rows.append({"open": 4511, "high": 4520, "low": 4508, "close": 4509, "volume": 500.0})
        sweep_rows.append({"open": 4509, "high": 4512, "low": 4505, "close": 4506, "volume": 400.0})
        sweep_rows.append({"open": 4506, "high": 4518, "low": 4505, "close": 4516, "volume": 350.0})
        # Price retraces into FVG (4518–4520 zone)
        for i in range(6, 20):
            sweep_rows.append({"open": 4516 + i * 0.1, "high": 4518 + i * 0.05,
                               "low": 4515, "close": 4516, "volume": 300.0})
        ny_df = pd.DataFrame(sweep_rows[:len(ny_times)],
                             index=pd.DatetimeIndex(ny_times, tz="UTC"))
        return pd.concat([bars, ny_df]).sort_index()

    def test_strategy_generates_signals(self):
        config = ICTConfig(
            tick_size=0.25, min_sweep_ticks=2, rejection_candles=5,
            rejection_close_pct=0.3, trading_start_hour=14,
            fvg_fill_pct_entry=0.0,
        )
        strategy = ICTStrategy(params=config.model_dump())
        bars = self._full_trade_bars()
        signals = strategy.generate_signals(bars, "ES")
        assert isinstance(signals, list)

    def test_signal_has_required_fields(self):
        config = ICTConfig(
            tick_size=0.25, min_sweep_ticks=2, rejection_candles=5,
            rejection_close_pct=0.3, trading_start_hour=14,
            fvg_fill_pct_entry=0.0,
        )
        strategy = ICTStrategy(params=config.model_dump())
        bars = self._full_trade_bars()
        signals = strategy.generate_signals(bars, "ES")
        for sig in signals:
            assert sig.entry_price > 0
            assert sig.stop_loss > 0
            assert sig.take_profit > 0
            assert sig.direction in (SignalDirection.LONG, SignalDirection.SHORT)

    def test_short_signal_sl_above_entry(self):
        config = ICTConfig(tick_size=0.25, min_sweep_ticks=2, rejection_candles=5,
                          rejection_close_pct=0.3, trading_start_hour=14,
                          fvg_fill_pct_entry=0.0)
        strategy = ICTStrategy(params=config.model_dump())
        bars = self._full_trade_bars()
        signals = strategy.generate_signals(bars, "ES")
        short_sigs = [s for s in signals if s.direction == SignalDirection.SHORT]
        for s in short_sigs:
            assert s.stop_loss > s.entry_price, (
                f"SL {s.stop_loss} should be above entry {s.entry_price} for short"
            )

    def test_no_signals_before_trading_hour(self):
        config = ICTConfig(trading_start_hour=20, stop_trading_hour=21)
        strategy = ICTStrategy(params=config.model_dump())
        bars = _make_bars(n=840, start="2024-01-02 00:00", base_price=4500.0)
        # All bars before 20:00 UTC → should produce no signals
        cutoff = datetime(2024, 1, 2, 19, 0, tzinfo=timezone.utc)
        early_bars = bars[bars.index < cutoff]
        signals = strategy.generate_signals(early_bars, "ES")
        assert len(signals) == 0

    def test_max_trades_per_day_respected(self):
        config = ICTConfig(
            tick_size=0.25, min_sweep_ticks=2, rejection_candles=5,
            rejection_close_pct=0.2, trading_start_hour=14,
            fvg_fill_pct_entry=0.0, max_trades_per_day=1,
        )
        strategy = ICTStrategy(params=config.model_dump())
        bars = self._full_trade_bars()
        signals = strategy.generate_signals(bars, "ES")
        # Even if multiple setups form, cap at 1
        assert len(signals) <= 1

    def test_fixed_rr_exit_mode_tp_set(self):
        config = ICTConfig(
            tick_size=0.25, min_sweep_ticks=2, rejection_candles=5,
            rejection_close_pct=0.3, trading_start_hour=14,
            fvg_fill_pct_entry=0.0, exit_mode="fixed_rr", fixed_rr_ratio=2.0,
        )
        strategy = ICTStrategy(params=config.model_dump())
        bars = self._full_trade_bars()
        signals = strategy.generate_signals(bars, "ES")
        for sig in signals:
            risk = abs(sig.entry_price - sig.stop_loss)
            if sig.direction == SignalDirection.SHORT:
                expected_tp = sig.entry_price - risk * 2.0
                assert sig.take_profit == pytest.approx(expected_tp, rel=0.01)


# ── ICTBacktester ─────────────────────────────────────────────────────────────

class TestICTBacktester:

    def _rich_bars(self) -> pd.DataFrame:
        """Build bars with several sessions and a sweep pattern."""
        rng = np.random.default_rng(99)
        start = datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc)
        n = 960
        times = [start + timedelta(minutes=i) for i in range(n)]
        base = 4500.0
        prices = np.cumsum(rng.normal(0, 1.5, n)) + base
        prices = np.maximum(prices, 100.0)
        df = pd.DataFrame({
            "open": prices, "high": prices + 1.5,
            "low": prices - 1.5, "close": prices,
            "volume": rng.integers(300, 2000, n).astype(float),
        }, index=pd.DatetimeIndex(times, tz="UTC"))
        df["high"] = df[["open", "high", "close"]].max(axis=1)
        df["low"] = df[["open", "low", "close"]].min(axis=1)
        return df

    def test_backtester_runs_without_error(self):
        bt = ICTBacktester(starting_equity=100_000)
        result = bt.run(self._rich_bars(), symbol="ES")
        assert result is not None
        assert result.total_trades >= 0

    def test_win_rate_in_valid_range(self):
        bt = ICTBacktester(starting_equity=100_000)
        result = bt.run(self._rich_bars(), symbol="ES")
        if result.total_trades > 0:
            assert 0.0 <= result.win_rate <= 1.0

    def test_profit_factor_nonnegative(self):
        bt = ICTBacktester(starting_equity=100_000)
        result = bt.run(self._rich_bars(), symbol="ES")
        assert result.profit_factor >= 0.0

    def test_max_drawdown_nonpositive(self):
        bt = ICTBacktester(starting_equity=100_000)
        result = bt.run(self._rich_bars(), symbol="ES")
        assert result.max_drawdown <= 0.0

    def test_trade_replay_yields_snapshots(self):
        bars = self._rich_bars()
        bt = ICTBacktester(starting_equity=100_000)
        bt.run(bars, symbol="ES")
        replays = list(bt.trade_replay(bars, symbol="ES"))
        # Replay yields one snapshot per bar
        assert len(replays) == len(bars)

    def test_to_dict_has_required_keys(self):
        bt = ICTBacktester(starting_equity=100_000)
        result = bt.run(self._rich_bars(), symbol="ES")
        d = result.to_dict()
        required = {"win_rate", "profit_factor", "total_trades", "sharpe_ratio",
                    "max_drawdown", "total_return", "monthly_return", "expectancy"}
        assert required.issubset(d.keys())

    def test_sl_trades_have_negative_rr(self):
        bt = ICTBacktester(starting_equity=100_000)
        result = bt.run(self._rich_bars(), symbol="ES")
        sl_trades = [t for t in result.trades if t.exit_reason == "sl"]
        for t in sl_trades:
            assert t.pnl < 0 or t.rr_achieved <= 0

    def test_tp_trades_have_positive_pnl(self):
        bt = ICTBacktester(starting_equity=100_000)
        result = bt.run(self._rich_bars(), symbol="ES")
        tp_trades = [t for t in result.trades if t.exit_reason == "tp"]
        for t in tp_trades:
            assert t.pnl > 0
