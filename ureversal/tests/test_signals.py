import datetime as dt

import numpy as np
import pandas as pd
import pytest

from ureversal.config import load_config
from ureversal.signals import (
    BPS, SignalEngine, compute_features, rolling_corr_masked, rolling_slope, scan,
)
from ureversal.synth import default_planted_u, make_session


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def test_rolling_slope_matches_polyfit():
    rng = np.random.default_rng(0)
    x = np.cumsum(rng.normal(0, 1e-4, 200))
    w = 20
    got = rolling_slope(x, w)
    for t in (w - 1, 50, 199):
        want = np.polyfit(np.arange(w), x[t - w + 1 : t + 1], 1)[0] * BPS
        assert got[t] == pytest.approx(want, rel=1e-6)
    assert np.isnan(got[: w - 1]).all()


def test_rolling_corr_matches_pandas_when_all_valid():
    rng = np.random.default_rng(1)
    a, b = rng.normal(size=300), rng.normal(size=300)
    valid = np.ones(300, dtype=bool)
    got = rolling_corr_masked(a, b, valid, 30, 10)
    want = pd.Series(a).rolling(30).corr(pd.Series(b)).to_numpy()
    np.testing.assert_allclose(got[29:], want[29:], rtol=1e-8)


def test_rolling_corr_excludes_invalid_pairs():
    rng = np.random.default_rng(2)
    a, b = rng.normal(size=100), rng.normal(size=100)
    valid = np.ones(100, dtype=bool)
    valid[40:95] = False  # only ~5 valid pairs in trailing windows here
    got = rolling_corr_masked(a, b, valid, 30, 10)
    assert np.isnan(got[90])  # fewer than min_obs valid pairs


def test_detects_planted_pattern_and_quiet_on_control(cfg):
    hits, false_pos = 0, 0
    for seed in range(15):
        u = default_planted_u(offset_s=500 + seed * 10, lead_s=8, depth_bps=30)
        s = make_session(dt.date(2025, 6, 2), seed=seed, vol_bps_per_s=0.35, plant=[u])
        f = compute_features(s.bars, "SPY", "DIA", cfg.signals, cfg.min_corr_obs)
        trigs = scan(f, cfg.signals)
        hits += any(u.spy_bottom - 10 <= tr.t <= u.spy_bottom + 25 for tr in trigs)
        s0 = make_session(dt.date(2025, 6, 3), seed=1000 + seed, vol_bps_per_s=0.35)
        f0 = compute_features(s0.bars, "SPY", "DIA", cfg.signals, cfg.min_corr_obs)
        false_pos += len(scan(f0, cfg.signals))
    assert hits >= 10          # detector finds most planted patterns
    assert false_pos <= 2      # and stays quiet without them


def test_trigger_uses_no_lookahead(cfg):
    """Truncating the series after a trigger must not change the trigger."""
    u = default_planted_u(offset_s=500, lead_s=8, depth_bps=30)
    trigs, s = [], None
    for seed in range(10):  # detection isn't 100% — find a triggering seed
        s = make_session(dt.date(2025, 6, 2), seed=seed, vol_bps_per_s=0.35, plant=[u])
        f = compute_features(s.bars, "SPY", "DIA", cfg.signals, cfg.min_corr_obs)
        trigs = scan(f, cfg.signals)
        if trigs:
            break
    assert trigs
    t0 = trigs[0].t
    bars_cut = s.bars.iloc[: t0 + 1]
    f_cut = compute_features(bars_cut, "SPY", "DIA", cfg.signals, cfg.min_corr_obs)
    trigs_cut = scan(f_cut, cfg.signals)
    assert trigs_cut and trigs_cut[0].t == t0


def test_incremental_engine_parity_with_batch(cfg):
    u = default_planted_u(offset_s=450, lead_s=8, depth_bps=30)
    s = make_session(dt.date(2025, 6, 4), seed=5, vol_bps_per_s=0.35, plant=[u],
                     n_seconds=900)
    f = compute_features(s.bars, "SPY", "DIA", cfg.signals, cfg.min_corr_obs)
    batch = [tr.t for tr in scan(f, cfg.signals)]

    eng = SignalEngine(cfg)
    inc = []
    tc = s.bars[("SPY", "close")].to_numpy()
    lc = s.bars[("DIA", "close")].to_numpy()
    for t in range(len(s.bars)):
        trig = eng.update(s.bars.index[t], tc[t], True, lc[t], True)
        if trig:
            inc.append(t)
    assert inc == batch
