"""Position sizing with a hard 1%-risk cap and whole-share rounding.

    risk_dollars   = risk_fraction * risk_equity
    risk_per_share = expected_entry_price - initial_stop_price
    raw_shares     = floor(risk_dollars / risk_per_share)
    shares         = min(raw_shares, buying_power_cap, allocation_cap, liquidity_cap)

Rejects the trade when entry <= stop, stop distance is out of bounds, or size < 1 share.
Sizing uses a CONSERVATIVE expected fill price (includes allowed entry slippage) so the
realized risk cannot exceed the plan. Share count is never increased after a worse fill.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


class SizingReject(str, Enum):
    ENTRY_NOT_ABOVE_STOP = "ENTRY_NOT_ABOVE_STOP"
    STOP_TOO_TIGHT = "STOP_TOO_TIGHT"
    STOP_TOO_WIDE = "STOP_TOO_WIDE"
    SIZE_BELOW_ONE_SHARE = "SIZE_BELOW_ONE_SHARE"


@dataclass
class SizingResult:
    accepted: bool
    shares: int = 0
    risk_dollars: float = 0.0
    risk_per_share: float = 0.0
    stop_distance_pct: float = 0.0
    raw_shares: int = 0
    binding_cap: str | None = None
    reject_reason: SizingReject | None = None
    caps: dict[str, int] = field(default_factory=dict)


def compute_size(
    *,
    expected_entry_price: float,
    initial_stop_price: float,
    risk_equity: float,
    risk_fraction: float,
    buying_power: float,
    max_position_notional: float,
    liquidity_cap_shares: int | None,
    cfg_risk: dict,
    already_committed_risk: float = 0.0,
) -> SizingResult:
    """Compute whole-share size honoring the 1% rule and all caps.

    ``cfg_risk`` is the ``risk`` sub-mapping (for stop-distance bounds & risk_fraction ceiling).
    ``already_committed_risk`` is risk in dollars committed by open positions + pending orders;
    the per-trade budget is reduced by it so portfolio risk stays bounded.
    """
    if expected_entry_price <= 0:
        raise ValueError("expected_entry_price must be positive")
    if risk_fraction <= 0 or risk_fraction > float(cfg_risk.get("risk_fraction", 0.01)):
        # risk_fraction is configurable DOWN only; never above the config ceiling.
        raise ValueError("risk_fraction out of allowed range")

    # Guard: entry must be strictly above stop (long only).
    if expected_entry_price <= initial_stop_price:
        return SizingResult(accepted=False, reject_reason=SizingReject.ENTRY_NOT_ABOVE_STOP)

    risk_per_share = expected_entry_price - initial_stop_price
    stop_distance_pct = risk_per_share / expected_entry_price * 100.0

    min_pct = float(cfg_risk.get("min_stop_distance_pct", 0.5))
    max_pct = float(cfg_risk.get("max_stop_distance_pct", 15.0))
    if stop_distance_pct < min_pct:
        return SizingResult(
            accepted=False, risk_per_share=risk_per_share,
            stop_distance_pct=stop_distance_pct, reject_reason=SizingReject.STOP_TOO_TIGHT,
        )
    if stop_distance_pct > max_pct:
        return SizingResult(
            accepted=False, risk_per_share=risk_per_share,
            stop_distance_pct=stop_distance_pct, reject_reason=SizingReject.STOP_TOO_WIDE,
        )

    total_budget = risk_fraction * risk_equity
    remaining_budget = max(0.0, total_budget - already_committed_risk)
    risk_dollars = remaining_budget
    raw_shares = math.floor(risk_dollars / risk_per_share)

    # Caps (all whole shares).
    bp_cap = math.floor(buying_power / expected_entry_price)
    notional_cap = math.floor(max_position_notional / expected_entry_price)
    caps = {"risk": raw_shares, "buying_power": bp_cap, "notional": notional_cap}
    if liquidity_cap_shares is not None:
        caps["liquidity"] = int(liquidity_cap_shares)

    shares = min(caps.values())
    binding = min(caps, key=lambda k: caps[k])

    if shares < 1:
        return SizingResult(
            accepted=False, raw_shares=raw_shares, risk_per_share=risk_per_share,
            risk_dollars=risk_dollars, stop_distance_pct=stop_distance_pct,
            binding_cap=binding, caps=caps,
            reject_reason=SizingReject.SIZE_BELOW_ONE_SHARE,
        )

    return SizingResult(
        accepted=True, shares=shares, raw_shares=raw_shares,
        risk_per_share=risk_per_share, risk_dollars=shares * risk_per_share,
        stop_distance_pct=stop_distance_pct, binding_cap=binding, caps=caps,
    )


def realized_risk(actual_entry_price: float, initial_stop_price: float, shares: int) -> float:
    """Actual risk after a fill = (entry - stop) * shares. Used to alert on overruns."""
    return (actual_entry_price - initial_stop_price) * shares
