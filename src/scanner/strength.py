"""Strength ranking and TC2000 candidate-agreement modes.

`rank_top_percentile` reproduces the "top 2% by lookback return" selection using
ONLY closes up to the evaluation bar (no future data). It is used both to
independently validate TC2000 output and for shadow ranking.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

from src.indicators import lookback_return_pct


def rank_top_percentile(
    symbol_closes: Mapping[str, Sequence[float]],
    lookback_bars: int,
    top_percentile: float,
) -> list[str]:
    """Return symbols in the top ``top_percentile`` percent by lookback return.

    Deterministic tie-break: higher return first, then symbol ascending.
    Symbols lacking enough history are excluded (cannot be ranked without future data).
    """
    if not (0 < top_percentile <= 100):
        raise ValueError("top_percentile must be in (0, 100]")

    scored: list[tuple[float, str]] = []
    for symbol, closes in symbol_closes.items():
        if len(closes) < lookback_bars + 1:
            continue
        ret = lookback_return_pct(closes, lookback_bars)
        scored.append((ret, symbol))

    if not scored:
        return []

    scored.sort(key=lambda t: (-t[0], t[1]))
    keep = max(1, math.ceil(len(scored) * top_percentile / 100.0))
    return [sym for _, sym in scored[:keep]]


def composite_strength(returns_by_scan: Mapping[str, float]) -> float:
    """Composite strength score = mean of available per-scan returns (union ranking)."""
    vals = [v for v in returns_by_scan.values() if v is not None]
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def agreement_sets(
    one_month: Sequence[str],
    three_month: Sequence[str],
    six_month: Sequence[str],
) -> dict[str, list[str]]:
    """Compute 3-of-3, 2-of-3, and union candidate sets from three scan symbol lists.

    Returns sorted lists for deterministic output.
    """
    s1, s2, s3 = set(one_month), set(three_month), set(six_month)
    union = s1 | s2 | s3

    def count(sym: str) -> int:
        return (sym in s1) + (sym in s2) + (sym in s3)

    return {
        "intersection_3_of_3": sorted(sym for sym in union if count(sym) == 3),
        "agreement_2_of_3": sorted(sym for sym in union if count(sym) >= 2),
        "union_ranked": sorted(union),
    }


def membership(sym: str, scans: Mapping[str, Sequence[str]]) -> dict[str, bool | int]:
    """Per-symbol membership record across named scans plus agreement count."""
    flags = {name: sym in set(members) for name, members in scans.items()}
    return {**flags, "agreement_count": sum(1 for v in flags.values() if v)}
