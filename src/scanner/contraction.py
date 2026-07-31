"""Pullback / contraction setup detection (deterministic, scored components).

Detects fractal pivots, range contraction, proximity to SMA10/20, declining
volume vs the 22-day volume EMA, and an optional narrow pre-breakout candle.

REQUIRED components (config-driven) gate the setup; the score only prioritizes.
An AI opinion can never override a required component.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from src.indicators import atr, ema_series, sma_series

Bar = Mapping[str, float]


@dataclass
class SetupResult:
    is_setup: bool
    breakout_level: float | None
    components: dict[str, bool] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)


def find_pivot_highs(highs: Sequence[float], strength: int) -> list[int]:
    """Indices that are strictly higher than ``strength`` bars on each side."""
    idxs = []
    for i in range(strength, len(highs) - strength):
        window = highs[i - strength:i] + highs[i + 1:i + 1 + strength]
        if all(highs[i] > h for h in window):
            idxs.append(i)
    return idxs


def find_pivot_lows(lows: Sequence[float], strength: int) -> list[int]:
    idxs = []
    for i in range(strength, len(lows) - strength):
        window = lows[i - strength:i] + lows[i + 1:i + 1 + strength]
        if all(lows[i] < l for l in window):
            idxs.append(i)
    return idxs


def _is_increasing(seq: Sequence[float]) -> bool:
    return all(b > a for a, b in zip(seq, seq[1:]))


def _is_decreasing(seq: Sequence[float]) -> bool:
    return all(b < a for a, b in zip(seq, seq[1:]))


def _flat_to_declining(seq: Sequence[float]) -> bool:
    """First-vs-last comparison: last average not above the first (allow small noise)."""
    if len(seq) < 2:
        return True
    return seq[-1] <= seq[0]


def detect_contraction(bars: Sequence[Bar], cfg: dict, full_bars: Sequence[Bar]) -> SetupResult:
    """Detect a contraction setup on the trailing consolidation window.

    ``bars`` = the candidate consolidation window (most recent bars).
    ``full_bars`` = a longer history used for ATR / volume-EMA / SMA context.
    ``cfg`` = the ``contraction`` sub-mapping of the strategy config.
    """
    res = SetupResult(is_setup=True, breakout_level=None)
    n = len(bars)

    min_len = int(cfg["min_length_bars"])
    max_len = int(cfg["max_length_bars"])
    if not (min_len <= n <= max_len):
        res.is_setup = False
        res.reasons.append(f"length_out_of_range({n})")
        return res

    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]

    # Breakout level = highest high across the validated consolidation.
    res.breakout_level = max(highs)
    res.metrics["breakout_level"] = res.breakout_level

    # --- Range contraction: split window in half; later half narrower. ---
    if cfg.get("require_range_contraction", True):
        half = n // 2
        early = highs[:half] and lows[:half]
        if half >= 1:
            early_range = max(highs[:half]) - min(lows[:half])
            late_range = max(highs[half:]) - min(lows[half:])
            contracted = late_range < early_range
            res.metrics["early_range"] = early_range
            res.metrics["late_range"] = late_range
        else:
            contracted = False
        res.components["range_contraction"] = contracted
        if not contracted:
            res.is_setup = False
            res.reasons.append("range_not_contracting")

    # --- Higher pivot lows and lower pivot highs (when enough bars exist). ---
    strength = int(cfg.get("pivot_strength", 2))
    need_hl = int(cfg.get("require_higher_pivot_lows", 2))
    need_lh = int(cfg.get("require_lower_pivot_highs", 2))
    piv_lows = [lows[i] for i in find_pivot_lows(lows, strength)]
    piv_highs = [highs[i] for i in find_pivot_highs(highs, strength)]

    if n >= (need_hl + 1) + 2 * strength:
        hl_ok = len(piv_lows) >= need_hl and _is_increasing(piv_lows[-need_hl:])
        res.components["higher_lows"] = hl_ok
        if not hl_ok:
            res.is_setup = False
            res.reasons.append("no_higher_lows")
    if n >= (need_lh + 1) + 2 * strength:
        lh_ok = len(piv_highs) >= need_lh and _is_decreasing(piv_highs[-need_lh:])
        res.components["lower_highs"] = lh_ok
        if not lh_ok:
            res.is_setup = False
            res.reasons.append("no_lower_highs")

    # --- Proximity to SMA10/20. ---
    prox = cfg.get("proximity_to_ma", {})
    ref_mas = list(prox.get("reference_mas", [10, 20]))
    max_pct = float(prox.get("max_distance_pct", 3.0))
    atr_period = int(prox.get("atr_period", 20))
    max_atr = float(prox.get("max_distance_atr", 1.0))
    last_close = closes[-1]
    last_low = lows[-1]
    prox_ok = False
    for p in ref_mas:
        if len(full_bars) < p:
            continue
        ma = sma_series([b["close"] for b in full_bars], p)[-1]
        dist_pct = abs(last_close - ma) / ma * 100.0 if ma else float("inf")
        near_pct = dist_pct <= max_pct
        near_atr = False
        if len(full_bars) >= atr_period + 1:
            a = atr(full_bars, atr_period)
            near_atr = a > 0 and min(abs(last_close - ma), abs(last_low - ma)) <= max_atr * a
        if near_pct or near_atr:
            prox_ok = True
            res.metrics[f"dist_pct_sma{p}"] = dist_pct
            break
    res.components["proximity_to_ma"] = prox_ok
    if not prox_ok:
        res.is_setup = False
        res.reasons.append("not_near_sma10_20")

    # --- Volume: below 22-day volume EMA AND flat-to-declining. ---
    volcfg = cfg.get("volume", {})
    vol_ema_period = 22
    if len(full_bars) >= vol_ema_period:
        vol_ema = ema_series([b["volume"] for b in full_bars], vol_ema_period)[-1]
        below = volumes[-1] < vol_ema
        res.metrics["vol_ema22"] = vol_ema
        if volcfg.get("require_below_volume_ema", True):
            res.components["volume_below_ema"] = below
            if not below:
                res.is_setup = False
                res.reasons.append("prebreakout_volume_not_below_ema")
    if volcfg.get("require_flat_to_declining", True):
        declining = _flat_to_declining(volumes)
        res.components["volume_flat_to_declining"] = declining
        if not declining:
            res.is_setup = False
            res.reasons.append("volume_not_declining")

    # --- Optional narrow candle (scored, not required). ---
    ncfg = cfg.get("narrow_candle", {})
    if ncfg.get("enabled", True) and len(full_bars) >= int(ncfg.get("atr_period", 20)) + 1:
        a = atr(full_bars, int(ncfg.get("atr_period", 20)))
        tr = bars[-1]["high"] - bars[-1]["low"]
        narrow = a > 0 and tr < float(ncfg.get("max_tr_fraction_of_atr", 0.6)) * a
        res.components["narrow_candle"] = narrow

    # --- Setup score from independently logged components. ---
    weights = cfg.get("_score_weights", {})
    res.score = sum(
        float(weights.get(name, 1.0)) for name, passed in res.components.items() if passed
    )
    return res
