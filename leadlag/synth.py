"""Synthetic ES/SPY session with a planted, known lead.

Model: a common efficient log-price W follows a random walk on the 50ms grid.
ES tracks W immediately (plus tick rounding to 0.25); SPY tracks W delayed by
`lag_ms` (plus penny rounding and bid-ask bounce). Every estimator in the
study must recover `lag_ms` — that is the pipeline's self-test, and the
strategy backtest must show positive edge for latency < lag and none beyond.
"""
from __future__ import annotations

import datetime as dt

import numpy as np

from .config import Config
from .data import ET, Grid, _ns


def make_grid(cfg: Config, day: dt.date, seed: int, lag_ms: int = 200,
              vol_bps_per_s: float = 1.2, es0: float = 6000.0,
              spy0: float = 600.0, spy_print_prob: float = 0.6,
              bounce_usd: float = 0.005) -> Grid:
    dt_ms = cfg.grid["base_dt_ms"]
    t0 = _ns(day, cfg.fetch_start)
    t1 = _ns(day, cfg.fetch_end)
    n = int((t1 - t0) // (dt_ms * 1_000_000))
    rng = np.random.default_rng(seed)

    sigma_step = vol_bps_per_s * 1e-4 * np.sqrt(dt_ms / 1000)
    w = np.cumsum(rng.normal(0, sigma_step, n))
    k = max(lag_ms // dt_ms, 1)
    w_lag = np.concatenate([np.full(k, w[0]), w[:-k]])

    es_px = np.round((es0 * np.exp(w)) / 0.25) * 0.25
    bounce = rng.choice([-bounce_usd, bounce_usd], n)
    spy_px = np.round(spy0 * np.exp(w_lag) + bounce, 2)

    real_spy = rng.random(n) < spy_print_prob
    x_spy = np.log(spy_px)
    # freeze SPY price between prints (last-trade semantics)
    idx = np.where(real_spy, np.arange(n), -1)
    np.maximum.accumulate(idx, out=idx)
    x_spy = np.where(idx >= 0, x_spy[np.clip(idx, 0, None)], np.nan)

    flow = rng.normal(0, 5, n) + 40 * np.sign(np.gradient(w))  # flow follows W
    s0 = (_ns(day, cfg.study_start) - t0) // (dt_ms * 1_000_000)
    s1 = (_ns(day, cfg.study_end) - t0) // (dt_ms * 1_000_000)
    study = np.zeros(n, dtype=bool)
    study[int(s0):int(s1)] = True

    return Grid(day=day, dt_ms=dt_ms, t0_ns=t0, x_es=np.log(es_px),
                x_spy=x_spy, real_es=np.ones(n, dtype=bool), real_spy=real_spy,
                vol_es=np.abs(rng.normal(20, 5, n)),
                vol_spy=np.abs(rng.normal(4000, 500, n)),
                flow_es=flow, study=study)
