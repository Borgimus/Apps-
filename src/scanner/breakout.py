"""Breakout evaluation: crossing, gap ceiling, and chase-distance limits.

v1 breakout level = highest validated contraction range high (from contraction.py).
Entry triggers when live price crosses ABOVE the level during regular hours, but is
rejected if the price gapped too far above the level or has already run beyond the
allowed chase distance. Volume elevation is checked separately (time-of-day-normalized).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BreakoutDecision(str, Enum):
    NO_CROSS = "NO_CROSS"          # price has not crossed the level yet
    TRIGGER = "TRIGGER"            # valid breakout, may proceed to entry checks
    GAP_REJECT = "GAP_REJECT"     # opened/gapped too far above the level
    CHASE_REJECT = "CHASE_REJECT" # price ran beyond allowed chase distance


@dataclass
class BreakoutEval:
    decision: BreakoutDecision
    level: float
    price: float
    distance_pct: float
    reason: str


def evaluate_breakout(
    level: float,
    last_price: float,
    reference_price: float,
    cfg: dict,
) -> BreakoutEval:
    """Evaluate a breakout.

    ``level``           : breakout level (validated range high).
    ``last_price``      : current live price.
    ``reference_price`` : prior close / pre-cross reference used for gap detection.
    ``cfg``             : the ``breakout`` sub-mapping of the strategy config.
    """
    if level <= 0:
        raise ValueError("breakout level must be positive")

    max_gap_pct = float(cfg.get("max_gap_above_level_pct", 2.0))
    max_chase_pct = float(cfg.get("max_chase_distance_pct", 1.0))
    distance_pct = (last_price - level) / level * 100.0

    # Not yet crossed.
    if last_price <= level:
        return BreakoutEval(BreakoutDecision.NO_CROSS, level, last_price, distance_pct, "no_cross")

    # Gap rejection: reference already opened above the level by more than max_gap.
    if reference_price > level:
        gap_pct = (reference_price - level) / level * 100.0
        if gap_pct > max_gap_pct:
            return BreakoutEval(
                BreakoutDecision.GAP_REJECT, level, last_price, distance_pct,
                f"gap_above_level({gap_pct:.2f}%>{max_gap_pct}%)",
            )

    # Chase rejection: price already ran too far above the level to enter.
    if distance_pct > max_chase_pct:
        return BreakoutEval(
            BreakoutDecision.CHASE_REJECT, level, last_price, distance_pct,
            f"chase_exceeded({distance_pct:.2f}%>{max_chase_pct}%)",
        )

    return BreakoutEval(BreakoutDecision.TRIGGER, level, last_price, distance_pct, "trigger")


def entry_limit_price(level: float, cfg: dict) -> float:
    """Marketable/stop-limit price = level plus the configured slippage ceiling."""
    order = cfg.get("order", {})
    slip_pct = float(order.get("slippage_ceiling_pct", 0.5))
    return round(level * (1.0 + slip_pct / 100.0), 4)


def relative_volume_ok(cumulative_volume: float, expected_by_now: float, cfg: dict) -> bool:
    """Time-of-day-normalized breakout-volume check.

    ``expected_by_now`` must be a time-of-day-normalized historical expectation, NOT a
    full-day figure. Callers that only have a raw full-day comparison must not use this.
    """
    volcfg = cfg.get("volume", {})
    if not volcfg.get("require_elevated_breakout_volume", True):
        return True
    if expected_by_now <= 0:
        return False  # fail closed without a valid normalized expectation
    rel = cumulative_volume / expected_by_now
    return rel >= float(volcfg.get("min_relative_volume", 1.5))
