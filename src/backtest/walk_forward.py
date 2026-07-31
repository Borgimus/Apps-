"""Walk-forward splitting and parameter-sensitivity reporting.

Enforces separate development, validation, and final out-of-sample datasets with NO overlap,
and reports metrics across a parameter grid WITHOUT selecting the maximum-return configuration —
stability and adequate sample size are what the report surfaces.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import pstdev
from typing import Callable, Sequence


@dataclass
class Split:
    development: tuple[int, int]     # [start, end) indices
    validation: tuple[int, int]
    final_oos: tuple[int, int]


def three_way_split(n: int, *, dev: float = 0.5, val: float = 0.25) -> Split:
    """Chronological split into development / validation / final out-of-sample.

    Fractions must leave a positive final-OOS window and never overlap.
    """
    if not (0 < dev < 1 and 0 < val < 1 and dev + val < 1):
        raise ValueError("invalid split fractions")
    d_end = int(n * dev)
    v_end = d_end + int(n * val)
    if not (0 < d_end < v_end < n):
        raise ValueError("split produced an empty window; need more data")
    return Split((0, d_end), (d_end, v_end), (v_end, n))


def walk_forward_windows(n: int, *, train: int, test: int, step: int | None = None):
    """Yield (train_slice, test_slice) index pairs, train strictly before test, no overlap."""
    if train <= 0 or test <= 0:
        raise ValueError("train/test must be positive")
    step = step or test
    start = 0
    while start + train + test <= n:
        tr = (start, start + train)
        te = (start + train, start + train + test)
        yield tr, te
        start += step


@dataclass
class GridPoint:
    params: dict
    metric_value: float


def sensitivity(run: Callable[[dict], float], grid: Sequence[dict]) -> dict:
    """Run ``run(params)->metric`` across a grid and report the distribution.

    Returns the per-point values plus mean/stdev so the operator can see whether the metric is
    stable across a region rather than spiking at one lucky point. Does NOT pick a winner.
    """
    points = [GridPoint(params=p, metric_value=float(run(p))) for p in grid]
    values = [pt.metric_value for pt in points]
    return {
        "points": [{"params": pt.params, "value": pt.metric_value} for pt in points],
        "mean": round(sum(values) / len(values), 6) if values else 0.0,
        "stdev": round(pstdev(values), 6) if len(values) > 1 else 0.0,
        "n": len(values),
    }
