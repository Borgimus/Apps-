"""Five-R partial profit and breakeven-stop logic.

Definitions from the ACTUAL fill:
    R = actual_entry_price - initial_stop_price
The 5R partial triggers the FIRST time price reaches actual_entry_price + 5R.
It sells a configured fraction (default 25%), never zero shares and never more than
the open quantity, then moves the remaining shares' stop to the actual VWAP entry price.

The partial executes EXACTLY ONCE. Callers persist the returned one-time transition so a
restart cannot repeat it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def r_value(actual_entry_price: float, initial_stop_price: float) -> float:
    r = actual_entry_price - initial_stop_price
    if r <= 0:
        raise ValueError("R must be positive (entry must be above stop)")
    return r


def five_r_target(actual_entry_price: float, initial_stop_price: float, r_multiple: float = 5.0) -> float:
    return actual_entry_price + r_multiple * r_value(actual_entry_price, initial_stop_price)


@dataclass
class PartialPlan:
    should_fire: bool
    sell_shares: int = 0
    remaining_shares: int = 0
    new_stop_price: float | None = None
    reason: str = ""


def plan_five_r_partial(
    *,
    high_since_entry: float,
    actual_entry_price: float,
    initial_stop_price: float,
    open_shares: int,
    vwap_entry_price: float,
    already_done: bool,
    cfg_partial: dict,
) -> PartialPlan:
    """Decide whether/how to execute the one-time 5R partial.

    ``high_since_entry`` : highest price observed since the entry fill (touch detection).
    ``already_done``     : the persisted one-time flag; if True this returns should_fire=False.
    ``cfg_partial``      : the ``partial_exit`` sub-mapping of the strategy config.
    """
    if already_done:
        return PartialPlan(False, reason="already_done")
    if open_shares < 1:
        return PartialPlan(False, reason="no_open_shares")

    r_mult = float(cfg_partial.get("r_multiple_trigger", 5.0))
    target = five_r_target(actual_entry_price, initial_stop_price, r_mult)
    if high_since_entry < target:
        return PartialPlan(False, reason="target_not_reached")

    fraction = float(cfg_partial.get("fraction", 0.25))
    rounding = cfg_partial.get("rounding", "floor")
    raw = open_shares * fraction
    sell = math.floor(raw) if rounding == "floor" else round(raw)

    # Never sell zero; never exceed open quantity.
    sell = max(1, min(sell, open_shares))
    remaining = open_shares - sell

    new_stop = None
    if cfg_partial.get("move_stop_to_breakeven", True) and remaining > 0:
        new_stop = vwap_entry_price

    return PartialPlan(
        should_fire=True,
        sell_shares=sell,
        remaining_shares=remaining,
        new_stop_price=new_stop,
        reason=f"reached_{r_mult}R",
    )
