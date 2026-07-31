"""Live management of an OPEN position: 5R partial + breakeven, daily-close exit, missing-stop.

Deterministic: given the current tracked state and market inputs it returns ordered INTENTS.
The orchestrator decides whether to act on them (only in PAPER_AUTO) and routes them to the
broker. This reuses the same tested rules as the backtester (five_r, final_exit) so live and
backtest behavior cannot diverge.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.strategy.final_exit import evaluate_final_exit
from src.strategy.five_r import five_r_target, plan_five_r_partial


@dataclass
class LivePosition:
    symbol: str
    trade_id: str
    open_shares: int
    entry_price: float          # actual VWAP entry
    initial_stop: float
    partial_done: bool = False
    has_stop: bool = False       # a protective stop is acknowledged at the broker
    high_since_entry: float = 0.0


@dataclass
class PartialIntent:
    kind: str = "PARTIAL"
    sell_shares: int = 0
    limit_price: float = 0.0
    new_stop_price: float | None = None   # move remaining stop to breakeven


@dataclass
class FinalExitIntent:
    kind: str = "FINAL_EXIT"
    sell_shares: int = 0


@dataclass
class MissingStopIntent:
    kind: str = "MISSING_STOP"
    reason: str = "position lacks acknowledged protective stop"


@dataclass
class ManageResult:
    intents: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def manage_open_position(
    pos: LivePosition,
    *,
    last_price: float,
    session_completed: bool,
    daily_close: float | None,
    sma10_at_close: float | None,
    config: dict,
    high_since_entry: float | None = None,
) -> ManageResult:
    """Return ordered intents for one open position. Never emits a sell exceeding the open qty.

    Ordering & precedence:
      1. If the position lacks a protective stop -> MISSING_STOP only (fail-closed; RISK_BLOCKED).
      2. A confirmed daily-close-below-SMA10 exit supersedes everything else (exit all remaining).
      3. Otherwise, the one-time 5R partial (with breakeven stop move) if its target is reached.
    """
    res = ManageResult()

    if not pos.has_stop:
        res.intents.append(MissingStopIntent())
        res.notes.append("missing_stop -> risk_blocked")
        return res

    # Final exit takes precedence (completed daily close below SMA10).
    if session_completed and daily_close is not None and sma10_at_close is not None:
        fx = evaluate_final_exit(session_completed=True, daily_close=daily_close,
                                 sma10_at_close=sma10_at_close, cfg_final=config["final_exit"])
        if fx.triggered:
            res.intents.append(FinalExitIntent(sell_shares=pos.open_shares))
            res.notes.append(fx.reason)
            return res

    # One-time 5R partial + breakeven stop move.
    high = high_since_entry if high_since_entry is not None else max(pos.high_since_entry, last_price)
    plan = plan_five_r_partial(
        high_since_entry=high, actual_entry_price=pos.entry_price,
        initial_stop_price=pos.initial_stop, open_shares=pos.open_shares,
        vwap_entry_price=pos.entry_price, already_done=pos.partial_done,
        cfg_partial=config["partial_exit"],
    )
    if plan.should_fire:
        target = five_r_target(pos.entry_price, pos.initial_stop,
                               float(config["partial_exit"].get("r_multiple_trigger", 5.0)))
        res.intents.append(PartialIntent(sell_shares=plan.sell_shares, limit_price=target,
                                         new_stop_price=plan.new_stop_price))
        res.notes.append(plan.reason)

    return res
