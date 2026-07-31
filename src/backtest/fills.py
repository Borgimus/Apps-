"""Fill and cost models for the backtester.

Models the frictions the paper broker omits so backtest results are not optimistic:
entry slippage, spread, partial fills, unfilled stop-limit orders, overnight gaps THROUGH
stops, commissions, and regulatory fees. All functions are deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FillOutcome(str, Enum):
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    UNFILLED = "UNFILLED"
    GAP_THROUGH = "GAP_THROUGH"   # stop gapped: filled worse than the stop price


@dataclass
class Fill:
    outcome: FillOutcome
    qty: int
    price: float
    note: str = ""


# --- Regulatory / commission costs -----------------------------------------
# Conservative provisional rates; configurable. Sells incur SEC + TAF fees.
SEC_FEE_RATE = 27.80 / 1_000_000        # $27.80 per $1M of sell proceeds (illustrative)
TAF_PER_SHARE = 0.000166                # FINRA TAF per share sold
TAF_CAP = 8.30                          # per-order cap


def commission(shares: int, per_share: float = 0.0, minimum: float = 0.0) -> float:
    """Broker commission. Alpaca paper is commission-free (0.0) but backtests may model a cost."""
    return max(minimum, shares * per_share) if shares > 0 else 0.0


def regulatory_fees(side: str, shares: int, price: float) -> float:
    """SEC + TAF fees apply to SELLS only."""
    if side != "sell" or shares <= 0:
        return 0.0
    proceeds = shares * price
    sec = proceeds * SEC_FEE_RATE
    taf = min(TAF_CAP, shares * TAF_PER_SHARE)
    return round(sec + taf, 4)


def total_costs(side: str, shares: int, price: float, *, per_share_commission: float = 0.0) -> float:
    return commission(shares, per_share_commission) + regulatory_fees(side, shares, price)


# --- Entry (stop-limit) -----------------------------------------------------
def simulate_entry(
    *, breakout_level: float, limit_price: float, bar_open: float, bar_high: float,
    slippage_pct: float, qty: int,
) -> Fill:
    """Simulate a stop-limit BUY on a daily bar.

    - No breakout if the bar's high never reaches the level -> UNFILLED.
    - Gap above the limit ceiling (open already above limit) -> UNFILLED (chase protection).
    - Otherwise fill at min(limit, level*(1+slippage)) but never below the open.
    """
    if bar_high < breakout_level:
        return Fill(FillOutcome.UNFILLED, 0, 0.0, "high below breakout level")
    if bar_open > limit_price:
        return Fill(FillOutcome.UNFILLED, 0, 0.0, "gapped above limit ceiling")
    ideal = breakout_level * (1.0 + slippage_pct / 100.0)
    price = min(limit_price, ideal)
    price = max(price, bar_open) if bar_open > breakout_level else price
    price = min(price, limit_price)
    return Fill(FillOutcome.FILLED, qty, round(price, 4), "entry filled")


# --- Protective stop (with overnight gap) -----------------------------------
def simulate_stop(*, stop_price: float, bar_open: float, bar_low: float, qty: int) -> Fill | None:
    """Simulate a protective SELL stop over a bar.

    - Gap-down through the stop (open <= stop) -> filled at the OPEN (worse than stop).
    - Intrabar touch (low <= stop < open) -> filled at the stop price.
    - Otherwise not triggered -> None.
    """
    if bar_open <= stop_price:
        return Fill(FillOutcome.GAP_THROUGH, qty, round(bar_open, 4), "gap through stop")
    if bar_low <= stop_price:
        return Fill(FillOutcome.FILLED, qty, round(stop_price, 4), "stop hit intrabar")
    return None


# --- Partial (5R target) ----------------------------------------------------
def simulate_partial(*, target_price: float, bar_high: float, qty: int,
                     available: int) -> Fill:
    """Simulate the 5R partial limit SELL. Fills at the target if the bar trades through it."""
    if qty <= 0 or available <= 0:
        return Fill(FillOutcome.UNFILLED, 0, 0.0, "nothing to sell")
    fill_qty = min(qty, available)
    if bar_high >= target_price:
        return Fill(FillOutcome.FILLED, fill_qty, round(target_price, 4), "5R partial filled")
    return Fill(FillOutcome.UNFILLED, 0, 0.0, "target not reached")
