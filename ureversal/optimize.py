"""Walk-forward parameter optimization (§8.4).

Expanding train window by calendar month, 1-month test folds, random search
over the yaml-declared grids. The same candidate list is used in every fold so
parameter *stability across folds* is directly observable — a strategy whose
chosen parameters jump around fold-to-fold fails validation regardless of its
pooled numbers.

Because the optimization grids only touch thresholds (never the rolling-window
lengths), features are computed once per session and shared by every candidate.
"""
from __future__ import annotations

import datetime as dt
import itertools
import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .backtest import Backtester, BacktestResult, Metrics, Trade, compute_metrics
from .config import Config, ExitParams, SignalParams
from .signals import Features, compute_features, scan

log = logging.getLogger("ureversal.optimize")

EXIT_KEYS = set(ExitParams.__dataclass_fields__)
SIGNAL_KEYS = set(SignalParams.__dataclass_fields__)


@dataclass
class Fold:
    test_month: str
    params: dict
    train_objective: float
    train_trades: int
    oos_metrics: Metrics
    oos_trades: list[Trade] = field(default_factory=list)


@dataclass
class WalkForwardResult:
    folds: list[Fold]
    oos_metrics: Metrics
    oos_trades: list[Trade]
    oos_daily_pnl: pd.Series
    candidates_tested: int

    def parameter_stability(self) -> pd.DataFrame:
        """Chosen parameter values per fold — eyeball (or test) stability."""
        return pd.DataFrame([f.params for f in self.folds],
                            index=[f.test_month for f in self.folds])


def sample_candidates(grids: dict, n: int, seed: int = 0) -> list[dict]:
    """Random unique combos from the cartesian grid, always including the
    all-defaults (empty) candidate. Falls back to the full grid if small."""
    keys = list(grids)
    full = math.prod(len(grids[k]) for k in keys)
    rng = np.random.default_rng(seed)
    if full <= n:
        return [{}] + [dict(zip(keys, combo)) for combo in itertools.product(*(grids[k] for k in keys))]
    seen, out = set(), [{}]
    while len(out) < n + 1:
        combo = tuple(grids[k][rng.integers(len(grids[k]))] for k in keys)
        if combo not in seen:
            seen.add(combo)
            out.append(dict(zip(keys, combo)))
    return out


def split_candidate(cand: dict) -> tuple[dict, dict]:
    sig = {k: v for k, v in cand.items() if k in SIGNAL_KEYS}
    ext = {k: v for k, v in cand.items() if k in EXIT_KEYS}
    unknown = set(cand) - SIGNAL_KEYS - EXIT_KEYS
    if unknown:
        raise KeyError(f"grid keys not in SignalParams/ExitParams: {unknown}")
    return sig, ext


class FeatureCache:
    """Features per session, computed once (grids never vary window lengths)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._store: dict[dt.date, Features] = {}

    def get(self, day: dt.date, bars: pd.DataFrame) -> Features:
        if day not in self._store:
            self._store[day] = compute_features(
                bars, self.cfg.target, self.cfg.leader, self.cfg.signals,
                self.cfg.min_corr_obs, self.cfg.exits.atr_window_s,
            )
        return self._store[day]


def _evaluate(
    bt: Backtester, cfg: Config, days: list[dt.date],
    sessions: dict[dt.date, pd.DataFrame], cache: FeatureCache, cand: dict,
) -> BacktestResult:
    sig_o, ext_o = split_candidate(cand)
    p = cfg.signals.override(**sig_o)
    x = cfg.exits.override(**ext_o)
    trades: list[Trade] = []
    daily: dict[dt.date, float] = {}
    skipped = 0
    for day in days:
        bars = sessions[day]
        f = cache.get(day, bars)
        trigs = scan(f, p)
        ts, sk = bt.run_session(bars, day, p, x, features=f, triggers=trigs)
        trades.extend(ts)
        skipped += sk
        daily[day] = sum(t.net_pnl for t in ts)
    dser = pd.Series(daily).sort_index()
    return BacktestResult(trades, dser, compute_metrics(trades, dser, bt.equity, skipped), p, x, skipped)


def _objective(metrics: Metrics, name: str, min_trades: int) -> float:
    if metrics.n_trades < min_trades:
        return -math.inf
    val = {
        "net_expectancy": metrics.expectancy_bps,
        "sharpe": metrics.sharpe_trade,
        "profit_factor": metrics.profit_factor,
    }[name]
    return val if np.isfinite(val) else -math.inf


def walk_forward(
    cfg: Config,
    sessions: dict[dt.date, pd.DataFrame],
    budget: int = 60,
    seed: int = 0,
) -> WalkForwardResult:
    opt = cfg.optimize
    days = sorted(sessions)
    months = sorted({d.strftime("%Y-%m") for d in days})
    by_month = {m: [d for d in days if d.strftime("%Y-%m") == m] for m in months}

    candidates = sample_candidates(opt["grids"], budget, seed)
    bt = Backtester(cfg)
    cache = FeatureCache(cfg)

    folds: list[Fold] = []
    oos_trades: list[Trade] = []
    oos_daily: dict[dt.date, float] = {}

    for k in range(opt["train_min_months"], len(months)):
        train_days = [d for m in months[:k] for d in by_month[m]]
        test_days = by_month[months[k]]
        best, best_obj, best_res = None, -math.inf, None
        for cand in candidates:
            res = _evaluate(bt, cfg, train_days, sessions, cache, cand)
            obj = _objective(res.metrics, opt["objective"], opt["min_trades_per_fold"])
            if obj > best_obj:
                best, best_obj, best_res = cand, obj, res
        if best is None or not np.isfinite(best_obj):
            log.info("fold %s: no candidate met min-trades — skipping", months[k])
            continue
        test_res = _evaluate(bt, cfg, test_days, sessions, cache, best)
        folds.append(
            Fold(
                test_month=months[k], params=best, train_objective=best_obj,
                train_trades=best_res.metrics.n_trades,
                oos_metrics=test_res.metrics, oos_trades=test_res.trades,
            )
        )
        oos_trades.extend(test_res.trades)
        oos_daily.update(test_res.daily_pnl.to_dict())
        log.info(
            "fold %s: train_obj=%.2f (%d trades) → oos %d trades, exp %.2f bps",
            months[k], best_obj, best_res.metrics.n_trades,
            test_res.metrics.n_trades, test_res.metrics.expectancy_bps,
        )

    dser = pd.Series(oos_daily).sort_index()
    return WalkForwardResult(
        folds=folds,
        oos_metrics=compute_metrics(oos_trades, dser, bt.equity),
        oos_trades=oos_trades,
        oos_daily_pnl=dser,
        candidates_tested=len(candidates),
    )
