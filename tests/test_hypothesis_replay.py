from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pytest

from app.research.equity_observer import ET
from app.research.replay.engine import HypothesisSignal, LookAheadError, outcome, run
from app.research.replay.portfolio import policy_session, scenarios
from app.research.replay.sources import HistoricalSource, index_current
from app.research.replay.stats import comparison, split_statistics


def ts(day, hour, minute):
    return datetime.combine(day, time(hour, minute), ET)


def row(start, close):
    return {"t": start.isoformat(), "open": 100.0, "low": min(100, close),
            "high": max(100, close), "close": close, "volume": 1.0}


def fixture(day, moves=(5, 0)):
    rows = {s: [row(ts(day, 9, 30) + timedelta(minutes=5*i),
                    100.0 * (1 + move / 10000) if i == 7 else 100.0)
                for i in range(14)] for s, move in zip(("SPY", "QQQ"), moves)}
    return next(HistoricalSource(rows).sessions())


def signal(view):
    return [HypothesisSignal("test", "SPY", view.at)]


def test_historical_clock_rejects_even_completed_but_unavailable_bars():
    day = date(2026, 11, 20)
    session = fixture(day)
    def peeker(view):
        view.bar("SPY", ts(day, 9, 35))
        return []
    with pytest.raises(LookAheadError):
        run(session, peeker)
    found, = run(session, signal)
    assert found.decided_at == ts(day, 9, 36)
    assert outcome(found, index_current(session), "30m")["gross_bps"] == pytest.approx(5.0)
    assert outcome(found, index_current(session), "12:30") is None


def test_synthetic_bars_detect_a_planted_5_bps_edge():
    rng = np.random.default_rng(123)
    candidate, control = {}, {}
    for i in range(130):
        day = date(2020, 1, 2) + timedelta(days=i)
        move = rng.normal(0, 3)
        session = fixture(day, (move + 5 + rng.normal(0, 1.5), move))
        index = index_current(session)
        chosen, = run(session, signal)
        baseline = HypothesisSignal("control", "QQQ", chosen.decided_at)
        candidate[day] = outcome(chosen, index, "30m")["gross_bps"] - 10
        control[day] = outcome(baseline, index, "30m")["gross_bps"] - 10
    _, statistic = comparison(candidate, control, paired=True)
    assert statistic["t"] > 3
    assert statistic["mean_bps"] == pytest.approx(5.0, abs=0.4)
    assert split_statistics(candidate, control, candidate, paired=True)["holdout"]["candidate_days"] == 39


def test_reusing_frozen_policy_preserves_day_gate_and_missing_bar():
    session = fixture(date(2026, 11, 20))
    initial, = run(session, signal)
    second = HypothesisSignal("second", "QQQ", ts(session.day, 10, 21))
    first = policy_session(session, [initial, second])
    report = scenarios(Path("."), [first])["10"]
    assert report["closed_proxies"] == 1
    assert report["unresolved"] == 1
    assert report["proxy_net_pnl_dollars"] is None
