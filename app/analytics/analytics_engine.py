"""
Analytics engine — computes performance metrics from the trade journal.

All methods query DBTradeJournal for closed trades only (status='closed').
Metrics are computed purely from stored data; no live broker calls.

Metrics provided
────────────────
summary()         : expectancy, win rate, avg winner/loser, Sharpe, max
                    drawdown, profit factor, total P&L, trade count.
by_strategy()     : same metrics broken down per strategy_id.
by_hour()         : metrics grouped by entry hour (ET).
by_weekday()      : metrics grouped by weekday (Mon–Fri).
by_delta_range()  : metrics grouped by delta bucket (0–0.3, 0.3–0.5, 0.5+).
by_iv_percentile(): metrics grouped by IV quartile.
equity_curve()    : chronological equity curve list.
spread_analysis() : avg spread %, avg fill slippage.
rejection_summary(): rejection counts broken down by reason.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.models import DBTradeJournal

logger = logging.getLogger(__name__)

_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _metrics(pnls: List[float]) -> Dict[str, Any]:
    """Compute standard metrics from a list of P&L values."""
    if not pnls:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "expectancy": 0.0,
            "profit_factor": 0.0,
            "total_pnl": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
        }

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / len(pnls)
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    # Equity-curve max drawdown
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # Annualised Sharpe (per-trade, scaled by sqrt(252))
    arr = np.array(pnls)
    sharpe = 0.0
    if len(arr) > 1 and arr.std() > 0:
        sharpe = float(arr.mean() / arr.std() * np.sqrt(252))

    return {
        "total_trades": len(pnls),
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "profit_factor": round(profit_factor, 4),
        "total_pnl": round(sum(pnls), 2),
        "max_drawdown": round(max_dd, 4),
        "sharpe_ratio": round(sharpe, 4),
    }


class AnalyticsEngine:
    """Compute performance metrics from the trade journal."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def _closed_trades(self) -> List[DBTradeJournal]:
        rows = (
            await self._db.execute(
                select(DBTradeJournal)
                .where(DBTradeJournal.status == "closed")
                .order_by(DBTradeJournal.exit_time)
            )
        ).scalars().all()
        return list(rows)

    # ── Public API ────────────────────────────────────────────────────────────

    async def summary(self) -> Dict[str, Any]:
        trades = await self._closed_trades()
        pnls = [t.realized_pnl for t in trades if t.realized_pnl is not None]
        m = _metrics(pnls)
        m["total_closed_trades"] = len(trades)
        holds = [t.hold_duration_secs for t in trades if t.hold_duration_secs is not None]
        m["avg_hold_secs"] = round(float(np.mean(holds)), 1) if holds else 0.0
        return m

    async def by_strategy(self) -> Dict[str, Dict[str, Any]]:
        trades = await self._closed_trades()
        buckets: Dict[str, List[float]] = defaultdict(list)
        for t in trades:
            if t.realized_pnl is not None:
                buckets[t.strategy_id].append(t.realized_pnl)
        return {k: _metrics(v) for k, v in sorted(buckets.items())}

    async def by_hour(self) -> Dict[str, Dict[str, Any]]:
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
        trades = await self._closed_trades()
        buckets: Dict[str, List[float]] = defaultdict(list)
        for t in trades:
            if t.realized_pnl is not None and t.entry_time is not None:
                et = (
                    t.entry_time.replace(tzinfo=ET)
                    if t.entry_time.tzinfo is None
                    else t.entry_time.astimezone(ET)
                )
                buckets[f"{et.hour:02d}:00"].append(t.realized_pnl)
        return {k: _metrics(v) for k, v in sorted(buckets.items())}

    async def by_weekday(self) -> Dict[str, Dict[str, Any]]:
        trades = await self._closed_trades()
        buckets: Dict[int, List[float]] = defaultdict(list)
        for t in trades:
            if t.realized_pnl is not None:
                wd = t.weekday if t.weekday is not None else (
                    t.entry_time.weekday() if t.entry_time else -1
                )
                if 0 <= wd <= 6:
                    buckets[wd].append(t.realized_pnl)
        return {
            _WEEKDAY_NAMES[wd]: _metrics(v)
            for wd, v in sorted(buckets.items())
        }

    async def by_delta_range(self) -> Dict[str, Dict[str, Any]]:
        trades = await self._closed_trades()
        buckets: Dict[str, List[float]] = defaultdict(list)
        for t in trades:
            if t.realized_pnl is None or t.delta is None:
                continue
            d = abs(t.delta)
            key = "0.0–0.3" if d < 0.3 else ("0.3–0.5" if d < 0.5 else "0.5+")
            buckets[key].append(t.realized_pnl)
        return {k: _metrics(v) for k, v in sorted(buckets.items())}

    async def by_iv_percentile(self) -> Dict[str, Dict[str, Any]]:
        trades = await self._closed_trades()
        ivs_pnl = [(t.iv, t.realized_pnl) for t in trades if t.iv is not None and t.realized_pnl is not None]
        if not ivs_pnl:
            return {}
        all_ivs = [x[0] for x in ivs_pnl]
        p25, p50, p75 = np.percentile(all_ivs, [25, 50, 75])
        buckets: Dict[str, List[float]] = defaultdict(list)
        for iv, pnl in ivs_pnl:
            if iv < p25:
                key = "low (<25th)"
            elif iv < p50:
                key = "mid-low (25–50th)"
            elif iv < p75:
                key = "mid-high (50–75th)"
            else:
                key = "high (>75th)"
            buckets[key].append(pnl)
        return {k: _metrics(v) for k, v in buckets.items()}

    async def equity_curve(self, starting_equity: float = 0.0) -> List[Dict[str, Any]]:
        """
        Chronological equity curve — one point per closed trade.
        Returns list of {trade_id, exit_time, pnl, cumulative_pnl, equity}.
        """
        trades = await self._closed_trades()
        cumulative = starting_equity
        curve = []
        for t in trades:
            pnl = t.realized_pnl or 0.0
            cumulative += pnl
            curve.append({
                "trade_id": t.id,
                "exit_time": str(t.exit_time) if t.exit_time else None,
                "strategy_id": t.strategy_id,
                "pnl": round(pnl, 2),
                "cumulative_pnl": round(cumulative - starting_equity, 2),
                "equity": round(cumulative, 2),
            })
        return curve

    async def spread_analysis(self) -> Dict[str, Any]:
        trades = await self._closed_trades()
        spreads = [t.spread_pct for t in trades if t.spread_pct is not None]
        slippages = [t.slippage for t in trades if t.slippage is not None]
        return {
            "avg_spread_pct": round(float(np.mean(spreads)), 4) if spreads else 0.0,
            "max_spread_pct": round(float(np.max(spreads)), 4) if spreads else 0.0,
            "avg_slippage": round(float(np.mean(slippages)), 4) if slippages else 0.0,
            "total_slippage_cost": round(float(np.sum(slippages)) * 100, 2) if slippages else 0.0,
            "n_with_spread_data": len(spreads),
            "n_with_fill_data": len(slippages),
        }

    async def rejection_summary(self) -> Dict[str, Any]:
        rows = (
            await self._db.execute(
                select(DBTradeJournal).where(DBTradeJournal.status == "rejected")
            )
        ).scalars().all()
        reason_counts: Dict[str, int] = defaultdict(int)
        for r in rows:
            reason = (r.rejection_reason or "unknown").split(";")[0].strip()[:64]
            reason_counts[reason] += 1
        return {
            "total_rejections": len(rows),
            "by_reason": dict(sorted(reason_counts.items(), key=lambda x: -x[1])),
        }
