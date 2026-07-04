import datetime as dt

import numpy as np
import pandas as pd
import pytest

from ureversal.backtest import Backtester, ExitEvaluator, compute_metrics
from ureversal.config import load_config
from ureversal.synth import default_planted_u, make_session


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def _sessions(n=20, plant_every=1, vol=0.35):
    out = []
    for i, d in enumerate(pd.bdate_range(dt.date(2025, 3, 3), periods=n)):
        plant = ([default_planted_u(offset_s=400 + i * 13, lead_s=8, depth_bps=30)]
                 if i % plant_every == 0 else [])
        out.append((d.date(), make_session(d.date(), seed=i, vol_bps_per_s=vol,
                                           plant=plant).bars))
    return out


def test_costs_are_charged(cfg):
    bt = Backtester(cfg)
    assert bt._buy_px(500.0) > 500.0
    assert bt._sell_px(500.0) < 500.0
    rt_cost_bps = (bt._buy_px(500.0) - bt._sell_px(500.0)) / 500.0 * 1e4
    assert rt_cost_bps > 1.0  # ≥1bp round trip on SPY at $500


def test_planted_sessions_profit_and_controls_flat(cfg):
    bt = Backtester(cfg)
    res = bt.run(_sessions())
    assert res.metrics.n_trades >= 5
    assert res.metrics.expectancy_bps > 5
    ctrl = [(d, make_session(d, seed=700 + i, vol_bps_per_s=0.35).bars)
            for i, (d, _) in enumerate(_sessions())]
    assert bt.run(ctrl).metrics.n_trades <= 2


def test_max_trades_per_day_enforced(cfg):
    bt = Backtester(cfg)
    day = dt.date(2025, 3, 3)
    plant = [default_planted_u(offset_s=off, lead_s=8, depth_bps=30)
             for off in (300, 550, 800, 1050, 1300)]
    bars = make_session(day, seed=42, vol_bps_per_s=0.35, plant=plant).bars
    trades, _ = bt.run_session(bars, day, cfg.signals, cfg.exits)
    assert len(trades) <= cfg.risk["max_trades_per_day"]


def test_entries_confined_to_window(cfg):
    bt = Backtester(cfg)
    res = bt.run(_sessions())
    for t in res.trades:
        tt = t.entry_ts.tz_convert("America/New_York").time()
        assert cfg.entry_start <= tt
        # fill is 1s after the last allowed trigger at the latest
        assert tt <= (dt.datetime.combine(dt.date.today(), cfg.last_entry)
                      + dt.timedelta(seconds=1)).time()
        assert t.exit_ts.tz_convert("America/New_York").time() <= cfg.hard_exit


def test_exit_evaluator_target_and_stop(cfg):
    x = cfg.exits.override(mode="fixed", fixed_target_pct=0.2, stop_loss_pct=0.15)
    ev = ExitEvaluator(x, cfg.signals, entry_px=100.0)
    assert ev.step(1, 100.05, np.nan, np.nan, np.nan, np.nan) is None
    assert ev.step(2, 100.21, np.nan, np.nan, np.nan, np.nan) == "target"
    ev2 = ExitEvaluator(x, cfg.signals, entry_px=100.0)
    assert ev2.step(1, 99.84, np.nan, np.nan, np.nan, np.nan) == "stop"


def test_exit_evaluator_time_stop(cfg):
    x = cfg.exits.override(mode="time", time_stop_s=60)
    ev = ExitEvaluator(x, cfg.signals, entry_px=100.0)
    assert ev.step(59, 100.0, np.nan, np.nan, np.nan, np.nan) is None
    assert ev.step(60, 100.0, np.nan, np.nan, np.nan, np.nan) == "time_stop"


def test_exit_evaluator_momentum(cfg):
    x = cfg.exits.override(mode="momentum")
    ev = ExitEvaluator(x, cfg.signals, entry_px=100.0)
    assert ev.step(1, 100.0, np.nan, 0.5, 0.1, 0.0) is None
    assert ev.step(2, 100.0, np.nan, -0.1, 0.1, 0.0) == "dia_momentum_lost"


def test_metrics_math():
    daily = pd.Series({dt.date(2025, 1, 2): 100.0, dt.date(2025, 1, 3): -50.0})
    from ureversal.backtest import Trade
    from ureversal.signals import Trigger

    def mk(pnl, bps):
        return Trade(day=dt.date(2025, 1, 2), entry_t=0, entry_ts=pd.Timestamp.now(tz="UTC"),
                     entry_px=100, exit_t=10, exit_ts=pd.Timestamp.now(tz="UTC"),
                     exit_px=100 + pnl / 10, qty=10, gross_pnl=pnl, fees=0,
                     net_pnl=pnl, net_ret_bps=bps, hold_s=10, exit_reason="t",
                     trigger=None)

    m = compute_metrics([mk(100, 20), mk(-50, -10)], daily, 100000)
    assert m.win_rate == 0.5
    assert m.profit_factor == pytest.approx(2.0)
    assert m.expectancy_bps == pytest.approx(5.0)
    assert m.max_drawdown_usd == pytest.approx(50.0)
