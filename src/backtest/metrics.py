"""Backtest performance metrics. Reporting only — never used to auto-select parameters."""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, median
from typing import Sequence

from src.backtest.engine import Trade


@dataclass
class Metrics:
    n_signals: int = 0
    n_trades: int = 0
    win_rate: float = 0.0
    expectancy_r: float = 0.0
    expectancy_dollars: float = 0.0
    profit_factor: float | None = None
    avg_winner: float = 0.0
    median_winner: float = 0.0
    avg_loser: float = 0.0
    median_loser: float = 0.0
    max_drawdown: float = 0.0
    avg_holding_days: float = 0.0
    total_costs: float = 0.0
    gap_losses: int = 0
    turnover_notional: float = 0.0
    extra: dict = field(default_factory=dict)


def _max_drawdown(equity_curve: Sequence[float]) -> float:
    peak = equity_curve[0] if equity_curve else 0.0
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)
    return round(max_dd, 4)


def compute_metrics(trades: Sequence[Trade], *, n_signals: int = 0,
                    starting_equity: float = 0.0) -> Metrics:
    m = Metrics(n_signals=n_signals, n_trades=len(trades))
    if not trades:
        return m

    pnls = [t.realized_pnl for t in trades]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p < 0]

    m.win_rate = round(len(winners) / len(trades), 4)
    m.expectancy_r = round(mean(t.r_multiple for t in trades), 4)
    m.expectancy_dollars = round(mean(pnls), 4)
    gross_win = sum(winners)
    gross_loss = abs(sum(losers))
    m.profit_factor = round(gross_win / gross_loss, 4) if gross_loss > 0 else None
    m.avg_winner = round(mean(winners), 4) if winners else 0.0
    m.median_winner = round(median(winners), 4) if winners else 0.0
    m.avg_loser = round(mean(losers), 4) if losers else 0.0
    m.median_loser = round(median(losers), 4) if losers else 0.0
    m.avg_holding_days = round(mean(t.holding_days for t in trades), 4)
    m.total_costs = round(sum(t.costs for t in trades), 4)
    m.gap_losses = sum(1 for t in trades if t.gap_loss)
    m.turnover_notional = round(sum(t.entry_price * t.shares for t in trades), 4)

    # Equity curve (cumulative realized P&L) for drawdown.
    equity, curve = starting_equity, [starting_equity]
    for p in pnls:
        equity += p
        curve.append(equity)
    m.max_drawdown = _max_drawdown(curve)
    return m
