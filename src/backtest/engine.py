"""Event-driven, point-in-time backtest engine (research only — never places broker orders).

Anti-lookahead guarantees:
  * Setups/indicators are computed only from CLOSED bars up to the current index.
  * A breakout is attempted on the NEXT bar using that bar's open/high — never its close or
    its (not-yet-formed) low.
  * The initial stop uses the arming bar's low (fully known before the breakout bar), never the
    breakout bar's full-session low. This is the backtest's lookahead-free proxy for
    "regular-session low observed up to the trigger".

Within a bar, the protective stop is evaluated BEFORE the profit target (pessimistic ordering),
so results are never optimistic when a single bar's range spans both.

Setup detection is injected (`setup_fn`) — it is already unit-tested in scanner/contraction — so
this module's job is the event loop, fills, risk sizing, the 5R-once partial, and exits.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from src.backtest.fills import (
    FillOutcome,
    simulate_entry,
    simulate_partial,
    simulate_stop,
    total_costs,
)
from src.indicators import sma
from src.risk.sizing import compute_size
from src.scanner.breakout import entry_limit_price
from src.strategy.five_r import five_r_target

Bar = dict


@dataclass
class SetupArm:
    breakout_level: float


@dataclass
class Trade:
    symbol: str
    entry_index: int
    entry_date: str
    entry_price: float
    initial_stop: float
    shares: int
    exit_index: int | None = None
    exit_date: str | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    partial_price: float | None = None
    partial_shares: int = 0
    realized_pnl: float = 0.0
    r_multiple: float = 0.0
    holding_days: int = 0
    gap_loss: bool = False
    costs: float = 0.0


@dataclass
class BacktestResult:
    symbol: str
    trades: list[Trade] = field(default_factory=list)
    n_signals: int = 0
    fingerprint: str = ""


def dataset_fingerprint(bars: Sequence[Bar]) -> str:
    """Stable hash of the input series so a run is reproducible/attributable to its data."""
    payload = json.dumps(
        [[b.get("date"), b["open"], b["high"], b["low"], b["close"], b["volume"]] for b in bars],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def run_backtest(
    bars: Sequence[Bar],
    *,
    symbol: str,
    config: dict,
    setup_fn: Callable[[list[Bar]], Optional[SetupArm]],
    risk_equity: float,
    per_share_commission: float = 0.0,
    min_history: int = 210,
    arm_ttl_bars: int = 5,
) -> BacktestResult:
    """Replay ``bars`` chronologically for a single symbol and return the trade log."""
    res = BacktestResult(symbol=symbol, fingerprint=dataset_fingerprint(bars))
    cfg_bo = config["breakout"]
    cfg_risk = config["risk"]
    cfg_partial = config["partial_exit"]
    ref_ma = int(config["final_exit"].get("reference_ma", 10))
    slippage = float(cfg_bo.get("order", {}).get("slippage_ceiling_pct", 0.5))

    closes = [b["close"] for b in bars]

    position: Optional[Trade] = None
    partial_done = False
    pending_final_exit = False
    armed: Optional[SetupArm] = None
    armed_since = -1

    k = min_history
    while k < len(bars):
        bar = bars[k]

        # ---------------- manage an open position ----------------
        if position is not None:
            # Pending daily-close exit executes at THIS bar's open (next session open).
            if pending_final_exit:
                _close(position, k, bars[k], bars[k]["open"], "daily_close_exit",
                       per_share_commission, gap_ref=None)
                res.trades.append(position)
                position, partial_done, pending_final_exit = None, False, False
                k += 1
                continue

            remaining = position.shares - position.partial_shares

            # 1) Stop first (pessimistic within-bar ordering).
            stop_fill = simulate_stop(stop_price=position.initial_stop if not partial_done
                                      else position.entry_price,
                                      bar_open=bar["open"], bar_low=bar["low"], qty=remaining)
            if stop_fill is not None:
                gap = stop_fill.outcome is FillOutcome.GAP_THROUGH
                _close(position, k, bar, stop_fill.price,
                       "stop_gap" if gap else "stop", per_share_commission, gap_ref=gap)
                res.trades.append(position)
                position, partial_done, pending_final_exit = None, False, False
                k += 1
                continue

            # 2) 5R partial (exactly once).
            if not partial_done:
                target = five_r_target(position.entry_price, position.initial_stop,
                                       float(cfg_partial.get("r_multiple_trigger", 5.0)))
                sell = max(1, min(math.floor(remaining * float(cfg_partial.get("fraction", 0.25))),
                                  remaining))
                pf = simulate_partial(target_price=target, bar_high=bar["high"], qty=sell,
                                      available=remaining)
                if pf.outcome is FillOutcome.FILLED:
                    position.partial_price = pf.price
                    position.partial_shares += pf.qty
                    position.costs += total_costs("sell", pf.qty, pf.price,
                                                  per_share_commission=per_share_commission)
                    partial_done = True  # stop is now breakeven (entry) for the remainder

            # 3) Daily-close exit signal (confirm close < SMA_ref, act next open).
            if len(closes[:k + 1]) >= ref_ma:
                sma_ref = sma(closes[:k + 1], ref_ma)
                if bar["close"] < sma_ref:
                    pending_final_exit = True

            k += 1
            continue

        # ---------------- flat: arm and attempt entry ----------------
        if armed is None:
            arm = setup_fn(list(bars[: k + 1]))  # closed bars up to k
            if arm is not None:
                armed, armed_since = arm, k
                res.n_signals += 1
            k += 1
            continue

        # Armed: attempt entry on this (later) bar.
        if k - armed_since > arm_ttl_bars:
            armed = None
            continue  # re-evaluate arming on the same bar next loop

        level = armed.breakout_level
        limit = entry_limit_price(level, cfg_bo)
        # Size using arming-bar low as the lookahead-free initial-stop proxy.
        initial_stop = bars[armed_since]["low"]
        expected_entry = level * (1.0 + slippage / 100.0)
        sizing = compute_size(
            expected_entry_price=expected_entry, initial_stop_price=initial_stop,
            risk_equity=risk_equity, risk_fraction=float(cfg_risk["risk_fraction"]),
            buying_power=risk_equity, max_position_notional=float(cfg_risk["max_position_notional"]),
            liquidity_cap_shares=None, cfg_risk=cfg_risk,
        )
        if not sizing.accepted:
            armed = None
            continue

        fill = simulate_entry(breakout_level=level, limit_price=limit, bar_open=bar["open"],
                              bar_high=bar["high"], slippage_pct=slippage, qty=sizing.shares)
        if fill.outcome is FillOutcome.FILLED:
            position = Trade(symbol=symbol, entry_index=k, entry_date=str(bar.get("date")),
                             entry_price=fill.price, initial_stop=initial_stop,
                             shares=sizing.shares)
            position.costs += total_costs("buy", sizing.shares, fill.price,
                                          per_share_commission=per_share_commission)
            partial_done = False
            armed = None
        else:
            armed = None
        k += 1

    return res


def _close(trade: Trade, idx: int, bar: Bar, price: float, reason: str,
           per_share_commission: float, gap_ref) -> None:
    remaining = trade.shares - trade.partial_shares
    trade.exit_index = idx
    trade.exit_date = str(bar.get("date"))
    trade.exit_price = price
    trade.exit_reason = reason
    trade.holding_days = idx - trade.entry_index
    trade.gap_loss = bool(gap_ref)
    trade.costs += total_costs("sell", remaining, price, per_share_commission=per_share_commission)

    # Realized P&L across the (optional) partial and the final exit, net of costs.
    pnl = remaining * (price - trade.entry_price)
    if trade.partial_shares and trade.partial_price is not None:
        pnl += trade.partial_shares * (trade.partial_price - trade.entry_price)
    pnl -= trade.costs
    trade.realized_pnl = round(pnl, 4)

    r = trade.entry_price - trade.initial_stop
    if r > 0:
        # R multiple on total position using average exit vs entry (partials included).
        avg_exit_gain = pnl + trade.costs  # gross
        trade.r_multiple = round((avg_exit_gain / trade.shares) / r, 4)
