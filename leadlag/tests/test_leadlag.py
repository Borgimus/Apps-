import datetime as dt

import numpy as np
import pytest

from leadlag.config import load_config
from leadlag.phases import find_impulses, phase4_order_flow
from leadlag.stats import info_shares, xcorr_profile
from leadlag.strategy import run_combo
from leadlag.synth import make_grid


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def grids(cfg):
    return [make_grid(cfg, dt.date(2025, 6, 2) + dt.timedelta(days=i),
                      seed=i, lag_ms=200, vol_bps_per_s=3.0) for i in range(6)]


def test_xcorr_recovers_planted_lag(cfg, grids):
    steps = [max(1, ms // cfg.grid["base_dt_ms"]) for ms in cfg.grid["xcorr_lags_ms"]]
    peaks = []
    for g in grids:
        xc = xcorr_profile(g.x_es, g.x_spy, g.study, steps)
        finite = {k: v for k, v in xc.items() if np.isfinite(v) and k != 0}
        peaks.append(max(finite, key=finite.get) * cfg.grid["base_dt_ms"])
    assert 100 <= np.median(peaks) <= 300  # planted 200ms


def test_info_shares_identify_leader(cfg, grids):
    step = cfg.grid["var_dt_ms"] // cfg.grid["base_dt_ms"]
    cs = []
    for g in grids:
        r = info_shares(g.x_es[::step], g.x_spy[::step], g.study[::step], 3)
        if r:
            cs.append(r["gg_cs_es"])
    assert np.mean(cs) > 0.7
    # reversed roles → follower share
    r_rev = info_shares(grids[0].x_spy[::step], grids[0].x_es[::step],
                        grids[0].study[::step], 3)
    assert r_rev is None or r_rev["gg_cs_es"] < 0.5


def test_no_lag_no_lead(cfg):
    g = make_grid(cfg, dt.date(2025, 7, 1), seed=99, lag_ms=50, vol_bps_per_s=3.0)
    # lag = one grid step; xcorr mass should be near-symmetric beyond ±1 step
    steps = [5, 10, 20]
    xc = xcorr_profile(g.x_es, g.x_spy, g.study, steps)
    pos = sum(v for k, v in xc.items() if k > 1 and np.isfinite(v))
    assert abs(pos) < 0.15


def test_latency_kills_edge(cfg, grids):
    fast, _ = run_combo(cfg, grids, 500, 2, 5000, 0)
    slow, _ = run_combo(cfg, grids, 500, 2, 5000, 1000)
    assert len(fast) > 100 and fast.mean() > 0
    assert len(slow) == 0 or slow.mean() < fast.mean() / 3


def test_impulses_found_and_oriented(cfg, grids):
    evs = []
    for g in grids:
        evs.extend(find_impulses(cfg, g, 5))
    assert evs, "no impulses found at 5bp on vol=3bps/s synthetic"
    paths = np.array([e.sign * e.spy_path_bps for e in evs])
    assert paths[:, -1].mean() > 0  # SPY follows the ES impulse direction


def test_order_flow_ic_positive(cfg, grids):
    # synthetic flow follows the common factor → must predict SPY forward
    out = phase4_order_flow(cfg, grids)
    key = "imb1000ms_fwd500ms"
    assert out[key]["mean_rank_ic"] > 0
