"""Trend qualification via NORMALIZED SLOPES (never literal chart angle).

Provisional rules (all configurable via config/strategy.yaml -> trend):
  * SMA10/20/50 slopes strictly positive (> min_slope_pct)
  * preferred stack SMA10 > SMA20 > SMA50 (scored, not strictly required)
  * price above SMA50 at qualification
  * SMA200 flat-to-rising (slope >= sma200_min_slope_pct)
Each component result is returned independently; nothing is hidden behind a label.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from src.indicators import sma_series, slope_pct


@dataclass
class TrendResult:
    qualified: bool
    components: dict[str, bool] = field(default_factory=dict)
    slopes: dict[int, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def evaluate_trend(closes: Sequence[float], cfg: dict) -> TrendResult:
    """Evaluate trend qualification from a daily close series.

    ``cfg`` is the ``trend`` sub-mapping of the strategy config.
    """
    lookback = int(cfg["slope_lookback_bars"])
    require_positive = list(cfg.get("require_positive_slope", [10, 20, 50]))
    min_slope = float(cfg.get("min_slope_pct", 0.0))
    result = TrendResult(qualified=True)

    periods = [10, 20, 50, 200]
    latest: dict[int, float] = {}
    for p in periods:
        # Need enough closes for the MA series plus the slope lookback.
        if len(closes) < p + lookback:
            result.qualified = False
            result.reasons.append(f"insufficient_bars_for_sma{p}")
            continue
        series = sma_series(closes, p)
        latest[p] = series[-1]
        result.slopes[p] = slope_pct(series, lookback)

    # Required: positive slopes on the configured shorter MAs.
    for p in require_positive:
        ok = p in result.slopes and result.slopes[p] > min_slope
        result.components[f"slope{p}_positive"] = ok
        if not ok:
            result.qualified = False
            result.reasons.append(f"slope{p}_not_positive")

    # SMA200 flat-to-rising (curling up tolerance).
    if cfg.get("sma200_flat_to_rising", True):
        min200 = float(cfg.get("sma200_min_slope_pct", 0.0))
        ok = 200 in result.slopes and result.slopes[200] >= min200
        result.components["sma200_flat_to_rising"] = ok
        if not ok:
            result.qualified = False
            result.reasons.append("sma200_declining")

    # Price above SMA50 at qualification.
    if cfg.get("require_price_above_sma50", True):
        ok = 50 in latest and closes[-1] > latest[50]
        result.components["price_above_sma50"] = ok
        if not ok:
            result.qualified = False
            result.reasons.append("price_not_above_sma50")

    # Preferred stack (scored, not strictly required).
    if all(p in latest for p in (10, 20, 50)):
        stacked = latest[10] > latest[20] > latest[50]
        result.components["stack_10_20_50"] = stacked
        # If prefer_stack is set as strict, treat as required; default: scored only.
        if cfg.get("prefer_stack_10_20_50", True) and cfg.get("stack_required", False):
            if not stacked:
                result.qualified = False
                result.reasons.append("stack_not_ordered")

    return result
