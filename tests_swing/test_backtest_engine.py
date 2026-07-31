"""Event-driven engine: no-lookahead stop, gap-through, unfilled, 5R-once, daily-close, repro."""
import copy

from src.backtest.engine import SetupArm, run_backtest
from src.config import load_config


def bar(date, o, h, l, c, v=1_000_000):
    return {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": v}


def cfg(**over):
    c = load_config().as_dict()
    for k, v in over.items():
        c["final_exit"][k] = v
    return c


def arm_on_marker(level):
    def _fn(closed):
        return SetupArm(breakout_level=level) if closed and closed[-1]["date"] == "arm" else None
    return _fn


def test_initial_stop_uses_arming_bar_low_not_breakout_bar_low():
    bars = [
        bar("d0", 100, 101, 99, 100),
        bar("arm", 100, 101, 98, 100),     # arming bar low = 98
        bar("d2", 100, 102, 99, 101),      # breakout bar low = 99 (must NOT be used)
        bar("d3", 90, 92, 88, 89),         # gap through stop
    ]
    res = run_backtest(bars, symbol="AAA", config=cfg(), setup_fn=arm_on_marker(101),
                       risk_equity=100_000, min_history=1)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.initial_stop == 98        # arming bar low — lookahead-free
    assert t.initial_stop != 99        # not the breakout bar's low


def test_gap_through_stop_marks_gap_loss():
    bars = [
        bar("d0", 100, 101, 99, 100),
        bar("arm", 100, 101, 98, 100),
        bar("d2", 100, 102, 99, 101),
        bar("d3", 95, 96, 94, 95),         # opens 95 < stop 98 -> gap through at open
    ]
    res = run_backtest(bars, symbol="AAA", config=cfg(), setup_fn=arm_on_marker(101),
                       risk_equity=100_000, min_history=1)
    t = res.trades[0]
    assert t.exit_reason == "stop_gap" and t.gap_loss is True and t.exit_price == 95.0


def test_unfilled_stop_limit_produces_no_trade():
    bars = [
        bar("d0", 100, 101, 99, 100),
        bar("arm", 100, 101, 98, 100),
        bar("d2", 103, 104, 102, 103),     # opens above limit ceiling -> unfilled
    ]
    res = run_backtest(bars, symbol="AAA", config=cfg(), setup_fn=arm_on_marker(101),
                       risk_equity=100_000, min_history=1)
    assert res.n_signals == 1 and res.trades == []


def test_five_r_partial_once_then_daily_close_exit():
    c = cfg(reference_ma=3)   # short SMA so the daily-close signal is reachable
    bars = [
        bar("d0", 100, 101, 99, 100),
        bar("arm", 100, 101, 98, 100),      # stop 98
        bar("d2", 100, 120, 100, 118),      # entry ~101.505 (partial NOT checked on entry bar)
        bar("d3", 118, 125, 117, 124),      # high 125 >= 5R target 119.03 -> partial once
        bar("d4", 124, 126, 123, 125),      # high>=target again but partial already done
        bar("d5", 124, 124, 122, 108),      # close 108 < sma3 -> daily-close signal
        bar("d6", 107, 108, 106, 107),      # remaining sold at next open (107)
    ]
    res = run_backtest(bars, symbol="AAA", config=c, setup_fn=arm_on_marker(101),
                       risk_equity=100_000, min_history=1)
    t = res.trades[0]
    assert t.entry_price == 101.505
    assert t.partial_price == 119.03        # 101.505 + 5*(101.505-98)
    assert t.partial_shares > 0
    assert t.exit_reason == "daily_close_exit" and t.exit_price == 107.0
    # Exactly one partial: remaining + partial == original shares, partial recorded once.
    assert t.partial_shares < t.shares


def test_reproducible_from_same_dataset():
    bars = [
        bar("d0", 100, 101, 99, 100),
        bar("arm", 100, 101, 98, 100),
        bar("d2", 100, 102, 99, 101),
        bar("d3", 95, 96, 94, 95),
    ]
    r1 = run_backtest(copy.deepcopy(bars), symbol="AAA", config=cfg(),
                      setup_fn=arm_on_marker(101), risk_equity=100_000, min_history=1)
    r2 = run_backtest(copy.deepcopy(bars), symbol="AAA", config=cfg(),
                      setup_fn=arm_on_marker(101), risk_equity=100_000, min_history=1)
    assert r1.fingerprint == r2.fingerprint
    assert [t.realized_pnl for t in r1.trades] == [t.realized_pnl for t in r2.trades]
    assert r1.trades[0].exit_price == r2.trades[0].exit_price
