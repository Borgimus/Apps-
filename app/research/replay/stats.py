"""Dependence-aware diagnostics on daily or monthly clusters."""
from __future__ import annotations

from collections import defaultdict
from statistics import mean
import math

import numpy as np


def newey_west(values, lag):
    y = np.asarray(values, dtype=float)
    if lag < 0 or y.ndim != 1 or not np.isfinite(y).all():
        raise ValueError("Newey–West requires finite ordered observations and nonnegative lag")
    n = len(y)
    if n < 3:
        return {"n": n, "mean_bps": float(y.mean()) if n else None, "se_bps": None, "t": None}
    mu = float(y.mean())
    centered = y - mu
    l = min(lag, n - 1)
    lrv = float(centered @ centered / n)
    for k in range(1, l + 1):
        lrv += 2 * (1 - k / (l + 1)) * float(centered[k:] @ centered[:-k] / n)
    se = math.sqrt(max(0.0, lrv) / (n - 1))
    return {"n": n, "mean_bps": mu, "se_bps": se, "t": mu / se if se > 0 else None}


def block_bootstrap(values, *, block=5, draws=2000, seed=0):
    y = np.asarray(values, dtype=float)
    if y.ndim != 1 or block < 1 or draws < 1 or not np.isfinite(y).all():
        raise ValueError("Invalid bootstrap inputs")
    if len(y) < 3:
        return None
    block = min(block, len(y))
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, len(y), (draws, math.ceil(len(y) / block)))
    indices = (starts[..., None] + np.arange(block)) % len(y)
    sampled = y[indices.reshape(draws, -1)[:, :len(y)]].mean(axis=1)
    return [float(v) for v in np.quantile(sampled, [0.025, 0.975])]


def comparison(candidate, control, *, lag=6, paired=False):
    """Difference of sample means. Cluster scores by market day, including overlap."""
    if paired:
        if set(candidate) != set(control):
            raise ValueError("Paired controls require identical cluster dates")
        days = sorted(candidate)
        difference = [candidate[d] - control[d] for d in days]
        return difference, newey_west(difference, lag)
    if not candidate or not control:
        return [], newey_west([], lag)
    days = sorted(set(candidate) | set(control))
    ca, co = mean(candidate.values()), mean(control.values())
    # Center each group at its own mean. Influence scores use sample shares,
    # so sparse controls are not treated as zero-return observations.
    scores = [ca - co
              + ((candidate[d] - ca) * len(days) / len(candidate) if d in candidate else 0)
              - ((control[d] - co) * len(days) / len(control) if d in control else 0)
              for d in days]
    return scores, newey_west(scores, lag)


def split_statistics(candidate, control, calendar, *, lag=6, paired=False):
    calendar = sorted(calendar)
    middle, split = len(calendar) // 2, math.floor(0.7 * len(calendar))
    slices = {"all": calendar, "first_half": calendar[:middle], "second_half": calendar[middle:],
              "development": calendar[:split], "holdout": calendar[split:]}
    reports = {}
    for key, days in slices.items():
        c = {d: candidate[d] for d in days if d in candidate}
        p = {d: control[d] for d in days if d in control}
        scores, result = comparison(c, p, lag=lag, paired=paired)
        reports[key] = {**result, "candidate_days": len(c), "control_days": len(p),
                        "block_bootstrap_ci_bps": block_bootstrap(scores, block=lag or 1)}
    return reports


def yearly_concentration(candidate, control, *, paired=False):
    yearly = defaultdict(list)
    for day in sorted(set(candidate) | set(control)):
        yearly[day.year].append(day)
    totals = {}
    all_scores, _ = comparison(candidate, control, paired=paired)
    all_days = sorted(set(candidate) | set(control))
    if all_scores:
        score = dict(zip(all_days, all_scores))
        totals = {str(year): sum(score[d] for d in days) for year, days in yearly.items()}
    total = sum(totals.values())
    share = max((v / total for v in totals.values()), default=None) if total > 0 else None
    return totals, share
