"""Phases 5–6: latency-aware strategy backtest, walk-forward, ML scoring.

The central honesty device is the latency sweep: every signal decided at grid
step t executes at t+λ for λ ∈ {0,50,…,1000}ms. If expectancy is positive at
λ=0 but dies by λ=100ms, the "edge" is a latency mirage for a retail stack
(market-data hop + decision + order gateway ≈ 50–300ms round trip).
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import dataclass

import numpy as np

from .config import Config
from .data import Grid

log = logging.getLogger("leadlag.strategy")
BPS = 1e4


@dataclass
class ComboResult:
    lookback_ms: int
    threshold_bps: float
    hold_ms: int
    latency_ms: int
    n_trades: int
    expectancy_bps: float
    win_rate: float
    profit_factor: float
    t_stat: float


def _cost_bps(cfg: Config, px: float) -> float:
    s = cfg.strategy
    return ((s["spy_half_spread_usd"] / px) * BPS + s["slippage_bps"]) * 2 + \
        (2 * s["commission_per_share"] + s["sec_taf_per_share_sell"]) / px * BPS


def run_combo(cfg: Config, grids: list[Grid], lookback_ms: int, thr_bps: float,
              hold_ms: int, latency_ms: int) -> tuple[np.ndarray, list]:
    """Per-trade net returns (bps) for one parameter combo across sessions."""
    dt_ms = cfg.grid["base_dt_ms"]
    L = max(lookback_ms // dt_ms, 1)
    H = max(hold_ms // dt_ms, 1)
    lam = latency_ms // dt_ms
    thr = thr_bps / BPS
    rets, when = [], []
    for g in grids:
        x_es, x_spy = g.x_es, g.x_spy
        n = len(g)
        t = L
        while t < n - lam - H - 1:
            if not g.study[t]:
                t += 1
                continue
            mv = x_es[t] - x_es[t - L]
            if not np.isfinite(mv) or abs(mv) < thr:
                t += 1
                continue
            side = 1 if mv > 0 else -1
            e, xit = t + lam, t + lam + H
            pe, px_ = x_spy[e], x_spy[xit]
            if np.isfinite(pe) and np.isfinite(px_):
                gross = side * (px_ - pe) * BPS
                rets.append(gross - _cost_bps(cfg, math.exp(pe)))
                when.append((g.day, t))
            t = xit + 1          # one position at a time, no overlap
    return np.array(rets), when


def combo_stats(cfg, rets: np.ndarray, lb, thr, hold, lam) -> ComboResult:
    n = len(rets)
    if n == 0:
        return ComboResult(lb, thr, hold, lam, 0, math.nan, math.nan, math.nan, math.nan)
    wins, losses = rets[rets > 0], rets[rets < 0]
    pf = float(wins.sum() / -losses.sum()) if losses.sum() < 0 else math.inf
    t = float(rets.mean() / (rets.std(ddof=1) / math.sqrt(n))) if n > 2 and rets.std() > 0 else math.nan
    return ComboResult(lb, thr, hold, lam, n, float(rets.mean()),
                       float((rets > 0).mean()), pf, t)


def phase5_sweep(cfg: Config, grids: list[Grid]) -> list[ComboResult]:
    s = cfg.strategy
    out = []
    for lb in s["lookback_ms"]:
        for thr in s["threshold_bps"]:
            for hold in s["hold_ms"]:
                for lam in s["latency_ms"]:
                    rets, _ = run_combo(cfg, grids, lb, thr, hold, lam)
                    out.append(combo_stats(cfg, rets, lb, thr, hold, lam))
    return out


def phase6_walk_forward(cfg: Config, grids: list[Grid],
                        latency_ms: int) -> dict:
    """Monthly walk-forward at a FIXED latency assumption: choose (L,θ,H) on
    train months, evaluate next month, pool OOS."""
    s, v = cfg.strategy, cfg.validation
    months = sorted({g.day.strftime("%Y-%m") for g in grids})
    by_month = {m: [g for g in grids if g.day.strftime("%Y-%m") == m] for m in months}
    combos = [(lb, thr, hold) for lb in s["lookback_ms"]
              for thr in s["threshold_bps"] for hold in s["hold_ms"]]
    folds, oos_all = [], []
    for k in range(v["walk_forward_train_months"], len(months)):
        train = [g for m in months[max(0, k - v["walk_forward_train_months"]):k]
                 for g in by_month[m]]
        test = by_month[months[k]]
        best, best_exp = None, -math.inf
        for lb, thr, hold in combos:
            r, _ = run_combo(cfg, train, lb, thr, hold, latency_ms)
            if len(r) >= v["min_trades_per_fold"] and r.mean() > best_exp:
                best, best_exp = (lb, thr, hold), float(r.mean())
        if best is None:
            continue
        r_oos, _ = run_combo(cfg, test, *best, latency_ms)
        folds.append({"month": months[k], "params": best,
                      "train_exp_bps": best_exp, "oos_trades": len(r_oos),
                      "oos_exp_bps": float(r_oos.mean()) if len(r_oos) else math.nan})
        oos_all.extend(r_oos.tolist())
    oos = np.array(oos_all)
    res = {"latency_ms": latency_ms, "folds": folds, "oos_trades": len(oos)}
    if len(oos) > 5:
        wins, losses = oos[oos > 0], oos[oos < 0]
        pf = float(wins.sum() / -losses.sum()) if losses.sum() < 0 else math.inf
        res.update(oos_expectancy_bps=float(oos.mean()),
                   oos_win_rate=float((oos > 0).mean()),
                   oos_profit_factor=pf,
                   passes_pf_gate=bool(pf >= v["min_profit_factor"]),
                   param_stability=len({f["params"] for f in folds}) <= max(2, len(folds) // 3))
    return res


# ── ML scoring (Phase 5 extension) ─────────────────────────────────────────


def build_ml_dataset(cfg: Config, grids: list[Grid], stride_ms: int = 1000,
                     label_horizon_ms: int = 1000, latency_ms: int = 100,
                     max_rows: int = 400_000, seed: int = 0):
    dt_ms = cfg.grid["base_dt_ms"]
    stride = stride_ms // dt_ms
    H = label_horizon_ms // dt_ms
    lam = latency_ms // dt_ms
    lb = {ms: ms // dt_ms for ms in (100, 250, 500, 1000, 5000)}
    Xs, ys, metas = [], [], []
    for g in grids:
        n = len(g)
        idx = np.arange(max(lb.values()) + 1, n - lam - H - 1, stride)
        idx = idx[g.study[idx]]
        if not len(idx):
            continue
        flow = np.nan_to_num(g.flow_es)
        cflow = np.cumsum(flow)
        s0 = int(np.argmax(g.study))
        feats = []
        for ms, k in lb.items():
            feats.append((g.x_es[idx] - g.x_es[idx - k]) * BPS)          # ES velocity
        feats.append((g.x_es[idx] - 2 * g.x_es[idx - lb[500]] + g.x_es[idx - 2 * lb[500]]) * BPS)  # accel
        feats.append((g.x_spy[idx] - g.x_spy[idx - lb[500]]) * BPS)      # SPY velocity
        basis = g.x_es - g.x_spy
        bmean = np.nanmean(basis[g.study])
        feats.append((basis[idx] - bmean) * BPS)                          # basis deviation
        feats.append(cflow[idx] - cflow[idx - lb[1000]])                  # flow 1s
        feats.append(cflow[idx] - cflow[idx - lb[5000]])                  # flow 5s
        feats.append((idx - s0) * dt_ms / 60_000.0)                       # minutes after open
        X = np.column_stack(feats)
        y = (g.x_spy[idx + lam + H] - g.x_spy[idx + lam]) * BPS
        ok = np.isfinite(X).all(axis=1) & np.isfinite(y)
        Xs.append(X[ok]); ys.append(y[ok])
        metas.extend([(g.day, int(t)) for t in idx[ok]])
    X = np.vstack(Xs); y = np.concatenate(ys)
    if len(X) > max_rows:
        sel = np.random.default_rng(seed).choice(len(X), max_rows, replace=False)
        sel.sort()
        X, y = X[sel], y[sel]
        metas = [metas[i] for i in sel]
    names = [f"es_vel_{ms}ms" for ms in lb] + [
        "es_accel", "spy_vel_500ms", "basis_dev_bps", "flow_1s", "flow_5s", "min_after_open"]
    return X, y, metas, names


def phase5_ml(cfg: Config, grids: list[Grid], latency_ms: int = 100) -> dict:
    """Chronological split: does an ML score predict SPY forward returns OOS,
    and does trading its extreme deciles clear costs at the given latency?"""
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.linear_model import LinearRegression

    X, y, metas, names = build_ml_dataset(cfg, grids, latency_ms=latency_ms)
    if len(X) < 5000:
        return {"error": "insufficient rows", "rows": len(X)}
    days = np.array([m[0] for m in metas])
    order = np.argsort(days, kind="stable")
    X, y, days = X[order], y[order], days[order]
    cut = int(len(X) * 0.7)
    while cut < len(X) and days[cut] == days[cut - 1]:
        cut += 1
    Xtr, ytr, Xte, yte = X[:cut], y[:cut], X[cut:], y[cut:]

    out = {"rows_train": len(Xtr), "rows_test": len(Xte), "features": names,
           "latency_ms": latency_ms}
    px = 600.0
    cost = _cost_bps(cfg, px)
    for name, model in (("linear", LinearRegression()),
                        ("gbm", GradientBoostingRegressor(
                            n_estimators=200, max_depth=3, learning_rate=0.05,
                            subsample=0.7, random_state=0))):
        model.fit(Xtr, ytr)
        pred = model.predict(Xte)
        ic = float(np.corrcoef(pred, yte)[0, 1])
        q_hi, q_lo = np.quantile(pred, 0.9), np.quantile(pred, 0.1)
        long_net = yte[pred >= q_hi] - cost
        short_net = -yte[pred <= q_lo] - cost
        both = np.concatenate([long_net, short_net])
        out[name] = {
            "oos_ic": ic,
            "top_decile_long_net_bps": float(long_net.mean()),
            "bottom_decile_short_net_bps": float(short_net.mean()),
            "combined_net_bps": float(both.mean()),
            "combined_win_rate": float((both > 0).mean()),
            "n_signals": int(len(both)),
        }
        if name == "gbm":
            imp = getattr(model, "feature_importances_", None)
            if imp is not None:
                out["gbm_feature_importance"] = dict(
                    sorted(zip(names, imp.round(4).tolist()),
                           key=lambda kv: -kv[1]))
    return out
