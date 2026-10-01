"""Remaining daily-loss capacity, shared by the live risk check and replay.

A daily loss limit that only checks realized losses lets a session at -$200
of a $250 limit open a trade that can lose another $250. Entry must instead
fit inside what is left: the trade's loss at its stop is reserved up front.
Gaps can still exceed the stop; this bounds the designed risk, not slippage.
"""
from __future__ import annotations

from typing import Optional


def worst_case_trade_loss(premium: float, quantity: int, stop_loss_pct: float) -> float:
    """Dollar loss if a long option position exits at its configured stop."""
    if premium <= 0 or quantity <= 0:
        return 0.0
    return float(premium) * 100.0 * int(quantity) * max(0.0, min(1.0, float(stop_loss_pct)))


def exceeds_loss_capacity(realized_pnl: float, trade_loss: float, limit: Optional[float]) -> bool:
    """True when realized losses plus this trade's stop loss would pass the limit."""
    if limit is None or limit <= 0:
        return False
    return max(0.0, -float(realized_pnl)) + float(trade_loss) > float(limit) + 1e-9
