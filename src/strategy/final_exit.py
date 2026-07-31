"""Final-exit timing: exit remaining shares after a COMPLETED daily close < SMA10.

Default policy: confirm the completed daily close below SMA10, then submit an order for the
NEXT regular-session open. Intraday penetration below SMA10 is NOT a daily-close exit.
Overnight gap risk between the signal close and the next executable open is logged.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FinalExitSignal:
    triggered: bool
    policy: str
    reason: str
    signal_close: float | None = None
    sma10_at_close: float | None = None


def evaluate_final_exit(
    *,
    session_completed: bool,
    daily_close: float,
    sma10_at_close: float,
    cfg_final: dict,
) -> FinalExitSignal:
    """Evaluate the daily-close exit.

    ``session_completed`` must be True (the regular session has ended and the daily bar is
    final). If False, no exit is signaled — we never act on an intraday value as if it were
    the official close.
    """
    policy = cfg_final.get("policy", "confirm_close_then_next_open")
    if not session_completed:
        return FinalExitSignal(False, policy, "session_not_complete")

    if daily_close < sma10_at_close:
        return FinalExitSignal(
            True, policy, "daily_close_below_sma10",
            signal_close=daily_close, sma10_at_close=sma10_at_close,
        )
    return FinalExitSignal(False, policy, "close_not_below_sma10",
                           signal_close=daily_close, sma10_at_close=sma10_at_close)


def overnight_gap_pct(signal_close: float, next_open: float) -> float:
    """Report overnight gap between the signal close and the next executable open.

    Negative means the next open is below the signal close (gap-down risk realized).
    """
    if signal_close <= 0:
        raise ValueError("signal_close must be positive")
    return (next_open - signal_close) / signal_close * 100.0


def stop_would_trigger_overnight(stop_price: float, next_low: float) -> bool:
    """Whether the protective stop would have been hit by the next session's low
    (used to model gap-through-stop behavior in backtests and reconciliation)."""
    return next_low <= stop_price
