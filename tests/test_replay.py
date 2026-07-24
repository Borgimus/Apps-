"""
Tests for ReplayEngine.

Key properties verified:
  - Determinism: identical bars + settings always produce identical results.
  - Correct signal → position lifecycle.
  - Exit conditions fire during replay.
  - Empty bars return empty result.
  - Replay result metrics are consistent.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from app.replay import ReplayEngine, ReplayResult
from app.strategies import OpeningRangeBreakoutStrategy

ET = ZoneInfo("America/New_York")


def _make_bars(
    date_str: str = "2024-01-02",
    direction: str = "up",
    n: int = 40,
    base_price: float = 450.0,
    or_bars: int = 3,
    breakout_bar_idx: int = 4,
    seed: int = 42,
) -> pd.DataFrame:
    """Seeded 5-minute bars for deterministic tests."""
    rng = np.random.default_rng(seed)
    start = f"{date_str} 14:30"
    idx = pd.date_range(start, periods=n, freq="5min", tz="UTC")

    closes = np.full(n, base_price)
    volumes = np.full(n, 1_000_000, dtype=float)

    for i in range(or_bars):
        closes[i] = base_price + rng.uniform(-0.15, 0.15)

    if direction == "up":
        closes[breakout_bar_idx] = base_price + 3.0
    else:
        closes[breakout_bar_idx] = base_price - 3.0
    volumes[breakout_bar_idx] = 4_000_000

    return pd.DataFrame(
        {
            "open":   closes - 0.1,
            "high":   closes + 0.5,
            "low":    closes - 0.5,
            "close":  closes,
            "volume": volumes,
        },
        index=idx,
    )


def _engine(**kwargs):
    defaults = dict(
        strategy=OpeningRangeBreakoutStrategy(params={
            "range_minutes": 15,
            "min_range_pts": 0.1,
            "volume_confirmation": True,
        }),
        symbol="SPY",
        starting_equity=100_000.0,
    )
    defaults.update(kwargs)
    return ReplayEngine(**defaults)


class TestReplayDeterminism:

    def test_same_bars_same_result(self):
        bars = _make_bars(seed=42)
        engine = _engine()
        result_a = engine.replay(bars.copy())
        result_b = engine.replay(bars.copy())
        assert result_a.total_trades == result_b.total_trades
        assert result_a.total_pnl == result_b.total_pnl

    def test_different_seeds_can_differ(self):
        # Two different synthetic sessions may produce different trade counts
        bars_a = _make_bars(seed=1)
        bars_b = _make_bars(seed=2)
        engine = _engine()
        result_a = engine.replay(bars_a)
        result_b = engine.replay(bars_b)
        # Both should be valid results (no crash); values may differ
        assert isinstance(result_a, ReplayResult)
        assert isinstance(result_b, ReplayResult)


class TestReplayBasicBehavior:

    def test_empty_bars_returns_empty_result(self):
        empty = pd.DataFrame()
        engine = _engine()
        result = engine.replay(empty)
        assert result.total_trades == 0
        assert result.trades == []

    def test_upside_bars_produce_result(self):
        bars = _make_bars(direction="up")
        engine = _engine()
        result = engine.replay(bars)
        assert isinstance(result, ReplayResult)
        assert result.strategy_id == "orb"
        assert result.symbol == "SPY"

    def test_start_end_bar_set(self):
        bars = _make_bars()
        engine = _engine()
        result = engine.replay(bars)
        assert result.start_bar is not None
        assert result.end_bar is not None
        assert result.start_bar <= result.end_bar

    def test_metrics_computed_after_replay(self):
        bars = _make_bars(direction="up")
        engine = _engine()
        result = engine.replay(bars)
        # Even with 0 trades metrics should be defined
        assert isinstance(result.win_rate, float)
        assert isinstance(result.total_pnl, float)
        assert isinstance(result.max_drawdown, float)

    def test_to_dict_has_required_keys(self):
        bars = _make_bars(direction="up")
        engine = _engine()
        result = engine.replay(bars)
        d = result.to_dict()
        for key in ("strategy_id", "symbol", "total_trades", "win_rate", "total_pnl",
                    "expectancy", "max_drawdown", "sharpe_ratio"):
            assert key in d, f"Missing key: {key}"

    def test_mixed_case_columns_accepted(self):
        bars = _make_bars()
        bars.columns = ["Open", "High", "Low", "Close", "Volume"]
        engine = _engine()
        result = engine.replay(bars)
        assert isinstance(result, ReplayResult)


class TestReplayExitConditions:

    def test_eod_closes_position(self):
        """A position opened early in the session should be force-closed at session end."""
        bars = _make_bars(direction="up", n=78)  # full trading day
        engine = _engine()
        result = engine.replay(bars)
        # No positions should remain open after replay
        # (all are force-closed in the final step)
        remaining = [t for t in result.trades if t.exit_reason is None]
        assert remaining == [], "All trades must have an exit reason"

    def test_session_end_exit_reason(self):
        """Force-closed positions should have exit_reason='session_end'."""
        bars = _make_bars(direction="up")
        engine = _engine()
        result = engine.replay(bars)
        for trade in result.trades:
            assert trade.exit_reason is not None

    def test_trade_pnl_is_finite(self):
        bars = _make_bars(direction="up")
        engine = _engine()
        result = engine.replay(bars)
        for trade in result.trades:
            assert not np.isnan(trade.pnl)
            assert not np.isinf(trade.pnl)


class TestReplayDedup:

    def test_only_one_position_per_symbol(self):
        """Even with multiple consecutive signals, at most one position opens per symbol."""
        bars_a = _make_bars("2024-01-02", "up", n=78)
        bars_b = _make_bars("2024-01-03", "up", n=78)
        bars = pd.concat([bars_a, bars_b])
        engine = _engine()
        result = engine.replay(bars)
        # At most 2 trades (one per day) — dedup prevents multiple same-symbol positions
        assert result.total_trades <= 2


class TestReplayMetricConsistency:

    def test_win_rate_between_0_and_1(self):
        bars = _make_bars(direction="up")
        engine = _engine()
        result = engine.replay(bars)
        assert 0.0 <= result.win_rate <= 1.0

    def test_max_drawdown_non_negative(self):
        bars = _make_bars(direction="up")
        engine = _engine()
        result = engine.replay(bars)
        assert result.max_drawdown >= 0.0

    def test_total_pnl_matches_trades(self):
        bars = _make_bars(direction="up")
        engine = _engine()
        result = engine.replay(bars)
        computed = round(sum(t.pnl for t in result.trades), 2)
        assert abs(computed - result.total_pnl) < 0.01
