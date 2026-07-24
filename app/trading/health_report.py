"""
Paper session health report generator.

Queries DBTradeJournal and DBPendingOrder for today's session and
produces a structured dict suitable for logging, file output, and
the /session/summary dashboard endpoint.

All fields are computed from the DB so the report is reproducible and
can be re-generated after the fact by querying any past session date.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.models import DBPendingOrder, DBSessionLog, DBTradeJournal

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


class HealthReporter:
    """Generate a structured end-of-session report from DB state."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def generate(
        self,
        session_date: str,
        api_errors: int = 0,
        reconciliation_warnings: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Build the full health report for a session date.

        Parameters
        ----------
        session_date       : YYYY-MM-DD string matching DBTradeJournal.session_date
        api_errors         : count of broker API failures recorded by session runner
        reconciliation_warnings : list of reconciler / recovery warning strings
        """
        report: Dict[str, Any] = {
            "session_date": session_date,
            "generated_at": datetime.now(tz=ET).isoformat(),
            "api_errors": api_errors,
            "reconciliation_warnings": reconciliation_warnings or [],
        }

        # ── Trade journal stats ───────────────────────────────────────────────
        closed = await self._trades_by_status(session_date, "closed")
        rejected = await self._trades_by_status(session_date, "rejected")
        cancelled = await self._trades_by_status(session_date, "cancelled")
        still_open = await self._trades_by_status(session_date, "open")

        pnls = [(t.realized_pnl or 0.0) for t in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        report["trades"] = {
            "total_closed": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(closed), 4) if closed else 0.0,
            "still_open": len(still_open),
            "rejected": len(rejected),
            "cancelled": len(cancelled),
        }
        report["realized_pnl"] = round(sum(pnls), 2)
        report["unrealized_pnl"] = round(
            sum(t.unrealized_pnl or 0.0 for t in still_open), 2
        )
        report["avg_win"] = (
            round(sum(wins) / len(wins), 2) if wins else 0.0
        )
        report["avg_loss"] = (
            round(sum(losses) / len(losses), 2) if losses else 0.0
        )
        report["max_drawdown"] = self._max_drawdown(pnls)

        # ── Order / fill stats ────────────────────────────────────────────────
        pending_rows = await self._all_pending_orders(session_date)
        status_counts: Dict[str, int] = {}
        for r in pending_rows:
            status_counts[r.status] = status_counts.get(r.status, 0) + 1

        fills = sum(1 for r in pending_rows if r.status == "filled")
        partials = sum(1 for r in pending_rows if r.status == "partially_filled")
        cancels = sum(1 for r in pending_rows if r.status in ("cancelled", "canceled"))

        report["orders"] = {
            "submitted": len(pending_rows),
            "filled": fills,
            "partial_fills": partials,
            "cancelled": cancels,
            "stale_cancels": await self._count_stale_cancels(session_date),
            "rejected": len(rejected),
            "status_breakdown": status_counts,
        }

        # ── Strategy breakdown ────────────────────────────────────────────────
        by_strategy: Dict[str, Dict] = {}
        for t in closed:
            sid = t.strategy_id or "unknown"
            if sid not in by_strategy:
                by_strategy[sid] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
            pnl = t.realized_pnl or 0.0
            by_strategy[sid]["trades"] += 1
            by_strategy[sid]["pnl"] = round(by_strategy[sid]["pnl"] + pnl, 2)
            if pnl > 0:
                by_strategy[sid]["wins"] += 1
            else:
                by_strategy[sid]["losses"] += 1
        for d in by_strategy.values():
            d["win_rate"] = (
                round(d["wins"] / d["trades"], 4) if d["trades"] > 0 else 0.0
            )
        report["by_strategy"] = by_strategy

        return report

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _trades_by_status(self, session_date: str, status: str):
        result = await self._db.execute(
            select(DBTradeJournal)
            .where(DBTradeJournal.session_date == session_date)
            .where(DBTradeJournal.status == status)
            .order_by(DBTradeJournal.entry_time)
        )
        return list(result.scalars().all())

    async def _all_pending_orders(self, session_date: str):
        result = await self._db.execute(
            select(DBPendingOrder)
            .where(DBPendingOrder.session_date == session_date)
            .order_by(DBPendingOrder.submitted_at)
        )
        return list(result.scalars().all())

    async def _count_stale_cancels(self, session_date: str) -> int:
        result = await self._db.execute(
            select(func.count(DBSessionLog.id))
            .where(DBSessionLog.session_date == session_date)
            .where(DBSessionLog.event == "cancel")
            .where(DBSessionLog.message.like("%stale%"))
        )
        return result.scalar() or 0

    @staticmethod
    def _max_drawdown(pnls: List[float]) -> float:
        if not pnls:
            return 0.0
        equity, peak, max_dd = 0.0, 0.0, 0.0
        for p in pnls:
            equity += p
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return round(max_dd, 4)
