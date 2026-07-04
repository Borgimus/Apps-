"""End-to-end pipeline tests: data reconstruction, optimizer, research study,
replay parity — all on synthetic data with known ground truth."""
import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from ureversal.config import load_config
from ureversal.data import trades_to_second_bars
from ureversal.optimize import sample_candidates, split_candidate
from ureversal.replay import replay_session
from ureversal.research import run_study
from ureversal.synth import default_planted_u, make_session

ET = ZoneInfo("America/New_York")


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def test_trades_to_second_bars():
    day = dt.date(2025, 6, 2)
    start = dt.datetime(2025, 6, 2, 9, 30, tzinfo=ET)
    end = dt.datetime(2025, 6, 2, 9, 30, 10, tzinfo=ET)
    ts = [start + dt.timedelta(seconds=s, milliseconds=ms)
          for s, ms in [(0, 100), (0, 900), (2, 500), (7, 0)]]
    trades = pd.DataFrame({"price": [100.0, 100.1, 100.2, 100.3],
                           "size": [10, 20, 30, 40]},
                          index=pd.DatetimeIndex(ts))
    bars = trades_to_second_bars(trades, start, end, max_ffill_s=3)
    assert len(bars) == 10
    assert bars.iloc[0]["close"] == 100.1                    # last trade in second
    assert bars.iloc[0]["vwap"] == pytest.approx((100.0*10 + 100.1*20) / 30)
    assert bars.iloc[0]["volume"] == 30
    assert bool(bars.iloc[1]["real"]) is False
    assert bars.iloc[1]["close"] == 100.1                    # ffill
    assert np.isnan(bars.iloc[6]["close"])                   # gap > max_ffill
    assert bars.iloc[7]["close"] == 100.3


def test_sample_candidates_unique_and_include_default():
    grids = {"a": [1, 2, 3], "b": [10, 20]}
    cands = sample_candidates(grids, 100)
    assert cands[0] == {}          # default always included
    assert len(cands) == 7         # full grid (6) + default
    tuples = [tuple(sorted(c.items())) for c in cands[1:]]
    assert len(set(tuples)) == len(tuples)


def test_split_candidate_routes_keys():
    sig, ext = split_candidate({"min_correlation": 0.8, "time_stop_s": 60})
    assert sig == {"min_correlation": 0.8}
    assert ext == {"time_stop_s": 60}
    with pytest.raises(KeyError):
        split_candidate({"not_a_param": 1})


def test_research_study_recovers_planted_lead(cfg):
    sessions = {}
    for i, d in enumerate(pd.bdate_range(dt.date(2025, 1, 2), periods=40)):
        plant = ([default_planted_u(offset_s=350 + (i * 41) % 700, lead_s=8,
                                    depth_bps=30)] if i % 2 == 0 else [])
        sessions[d.date()] = make_session(d.date(), seed=i, vol_bps_per_s=0.35,
                                          plant=plant).bars
    res = run_study(cfg, sessions, horizons=(60,), seed=0)
    assert res["frequency"]["events"] >= 10
    # planted lead of 8s must be recovered as a positive, significant lead
    assert res["leadlag"]["dia_leads_xcorr"]
    assert 2 <= res["leadlag"]["timing_median_s"] <= 14
    h = res["edge_by_horizon"][60]
    assert h["mean_bps"] > 5 and h["positive_after_costs"]
    assert h["beats_null_random"]


def test_research_study_no_edge_without_pattern(cfg):
    sessions = {
        d.date(): make_session(d.date(), seed=500 + i, vol_bps_per_s=0.35).bars
        for i, d in enumerate(pd.bdate_range(dt.date(2025, 1, 2), periods=30))
    }
    res = run_study(cfg, sessions, horizons=(60,), seed=0)
    assert res["verdict"] == "FAIL"
    assert res["frequency"]["events"] < 30


def test_replay_parity(cfg):
    u = default_planted_u(offset_s=420, lead_s=8, depth_bps=30)
    day = dt.date(2025, 6, 2)
    bars = make_session(day, seed=11, vol_bps_per_s=0.35, plant=[u]).bars
    r = replay_session(cfg, bars, day)
    assert r.parity_ok
