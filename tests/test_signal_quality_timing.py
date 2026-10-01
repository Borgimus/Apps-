"""Quality scores depend only on what was known when the signal fired.

The VWAP scorer's former fourth point read the bar after the signal bar. VWAP
signals are stamped at their last confirmation bar, so a fresh signal could
never earn it while the same signal re-scored 5-10 minutes later could; the
quality gate therefore favoured stale signals.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from zoneinfo import ZoneInfo

from app.evaluation.shadow_eligibility import assess_entry_filters
from app.strategies.signal_quality import compute_signal_quality_score
from app.strategies.strategy_base import Signal, SignalDirection

ET = ZoneInfo("America/New_York")


def _bars(n=12):
    idx = pd.DatetimeIndex([pd.Timestamp("2026-09-15 09:30", tz=ET) + timedelta(minutes=5 * i)
                            for i in range(n)]).tz_convert("UTC")
    close = np.array([100, 99.6, 99.4, 99.3, 99.5, 101.2, 101.6, 101.9, 102.1, 102.3, 102.5, 102.7][:n])
    vol = np.array([1000] * 5 + [5000] + [3000] * 6, dtype=float)[:n]
    return pd.DataFrame({"open": close, "high": close + .25, "low": close - .25,
                         "close": close, "volume": vol}, index=idx)


@pytest.mark.parametrize("strategy", ["vwap_reclaim", "orb"])
def test_score_is_identical_whether_evaluated_fresh_or_minutes_later(strategy):
    bars = _bars()
    sig = Signal(symbol="TEST", strategy_id=strategy, direction=SignalDirection.LONG,
                 price=float(bars["close"].iloc[5]),
                 timestamp=bars.index[5].to_pydatetime(), confidence=1.0)
    fresh = compute_signal_quality_score(sig, bars.iloc[:6])
    later = [compute_signal_quality_score(sig, bars.iloc[:k]) for k in (7, 8, 12)]
    assert fresh is not None
    assert later == [fresh] * 3


def test_vwap_scale_tops_out_at_three():
    bars = _bars()
    sig = Signal(symbol="TEST", strategy_id="vwap_reclaim", direction=SignalDirection.LONG,
                 price=float(bars["close"].iloc[5]),
                 timestamp=bars.index[5].to_pydatetime(), confidence=1.0)
    assert compute_signal_quality_score(sig, bars) == 3.0


def test_scoring_failure_is_unscored_not_zero():
    bars = _bars().drop(columns=["volume"])
    sig = Signal(symbol="TEST", strategy_id="vwap_reclaim", direction=SignalDirection.LONG,
                 price=101.2, timestamp=_bars().index[5].to_pydatetime(), confidence=1.0)
    assert compute_signal_quality_score(sig, bars) is None


def test_unscored_signal_is_rejected_under_its_own_reason():
    from types import SimpleNamespace
    settings = SimpleNamespace(paper_scaled_sizing_enabled=False, paper_scaled_guardrails_enabled=False)
    # Guards off: quality is not assessed at all.
    assert "signal_quality_unavailable" not in assess_entry_filters(
        settings, symbol="SPY", direction="long", quality_score=None, market_regime="long",
        limit_price=1, entry_ask=1, contract_metadata={"liquidity_passed": True})["entry_filter_reasons"]
    guarded = SimpleNamespace(
        paper_scaled_sizing_enabled=False, paper_scaled_guardrails_enabled=True,
        paper_scaled_blocked_symbols="", paper_scaled_min_signal_quality=3.0,
        paper_scaled_market_regime_confirmation_enabled=False)
    # assess_entry_filters only applies guards when scaled sizing is enabled too.
    guarded.paper_scaled_sizing_enabled = True
    guarded.universe = SimpleNamespace(max_contracts_per_position=10)
    guarded.paper_scaled_premium_budget_dollars = 250.0
    reasons = assess_entry_filters(
        guarded, symbol="SPY", direction="long", quality_score=None, market_regime="long",
        limit_price=1, entry_ask=1, contract_metadata={"liquidity_passed": True})["entry_filter_reasons"]
    assert reasons == ["signal_quality_unavailable"]
