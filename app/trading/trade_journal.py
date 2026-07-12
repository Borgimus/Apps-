"""
Trade journal — async DB writer for DBTradeJournal and DBSessionLog tables.

Records four kinds of events:
  entry     — order placed (or simulated in dry-run / replay)
  exit      — position closed with realized P&L
  rejection — signal blocked before order placement
  cancel    — open order cancelled (stale / manual)

Also writes structured session log rows via log_event().
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from ..api.models import DBSessionLog, DBTradeJournal

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


class TradeJournal:
    """Thin async writer; call methods inside an active SQLAlchemy session."""

    def __init__(self, db: AsyncSession, is_paper: bool = True):
        self._db = db
        self._is_paper = is_paper

    # ── Entry ─────────────────────────────────────────────────────────────────

    async def record_entry(
        self,
        *,
        entry_time: datetime,
        strategy_id: str,
        signal_direction: str,
        underlying_symbol: str,
        underlying_price: float,
        option_symbol: str,
        expiration: str,
        strike: float,
        option_type: str,
        delta: Optional[float],
        iv: Optional[float],
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        spread_pct: Optional[float] = None,
        limit_price: float = 0.0,
        limit_price_mode: Optional[str] = None,
        fill_price: Optional[float] = None,
        quantity: int = 1,
        order_id: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> int:
        """Insert an open entry record and return its row id."""
        et = entry_time.astimezone(ET) if entry_time.tzinfo else entry_time.replace(tzinfo=ET)
        row = DBTradeJournal(
            entry_time=entry_time,
            session_date=et.strftime("%Y-%m-%d"),
            strategy_id=strategy_id,
            signal_direction=signal_direction,
            underlying_symbol=underlying_symbol,
            underlying_price=underlying_price,
            weekday=et.weekday(),
            option_symbol=option_symbol,
            expiration=expiration,
            strike=strike,
            option_type=option_type,
            delta=delta,
            iv=iv,
            bid=bid,
            ask=ask,
            spread_pct=spread_pct,
            limit_price=limit_price,
            limit_price_mode=limit_price_mode,
            fill_price=fill_price,
            quantity=quantity,
            filled_quantity=0,
            order_id=order_id,
            status="open",
            is_paper=self._is_paper,
            notes=notes,
        )
        self._db.add(row)
        await self._db.flush()
        logger.info(
            "Journal entry %d: %s %s @ %.4f",
            row.id, signal_direction, option_symbol, limit_price,
        )
        return row.id

    # ── Exit ──────────────────────────────────────────────────────────────────

    async def record_exit(
        self,
        journal_id: int,
        exit_time: datetime,
        exit_price: float,
        exit_reason: str,
        realized_pnl: float,
        hold_duration_secs: float,
        unrealized_pnl: Optional[float] = None,
        filled_quantity: Optional[int] = None,
        exit_bid: Optional[float] = None,
        exit_ask: Optional[float] = None,
        peak_price: Optional[float] = None,
        trough_price: Optional[float] = None,
        mfe: Optional[float] = None,
        mae: Optional[float] = None,
        exit_order_id: Optional[str] = None,
        exit_quote_bid: Optional[float] = None,
    ):
        """Update an open entry with exit details and mark it closed.

        exit_price must be the broker's actual average fill price.
        exit_quote_bid is the pre-order bid retained for slippage analysis.
        """
        row = await self._db.get(DBTradeJournal, journal_id)
        if row is None:
            logger.warning("Journal record %d not found for exit update", journal_id)
            return
        row.exit_time = exit_time
        row.exit_price = exit_price
        row.exit_reason = exit_reason
        row.realized_pnl = realized_pnl
        row.unrealized_pnl = unrealized_pnl
        row.hold_duration_secs = hold_duration_secs
        row.slippage = (
            (row.fill_price - row.limit_price)
            if row.fill_price is not None and row.limit_price is not None
            else None
        )
        if filled_quantity is not None:
            row.filled_quantity = filled_quantity
        if exit_bid is not None:
            row.exit_bid = exit_bid
        if exit_ask is not None:
            row.exit_ask = exit_ask
        if exit_bid is not None and exit_ask is not None:
            mid = (exit_bid + exit_ask) / 2
            row.exit_spread_pct = (exit_ask - exit_bid) / mid if mid > 0 else None
        if peak_price is not None:
            row.peak_price = peak_price
        if trough_price is not None:
            row.trough_price = trough_price
        if mfe is not None:
            row.mfe = mfe
        if mae is not None:
            row.mae = mae
        if exit_order_id is not None:
            row.exit_order_id = exit_order_id
        if exit_quote_bid is not None:
            row.exit_quote_bid = exit_quote_bid
        row.status = "closed"
        logger.info(
            "Journal exit %d: reason=%s pnl=%.2f hold=%.0fs",
            journal_id, exit_reason, realized_pnl, hold_duration_secs,
        )

    # ── Fill update ───────────────────────────────────────────────────────────

    async def record_fill(
        self,
        journal_id: int,
        fill_price: float,
        filled_quantity: int,
    ):
        """Update fill price, quantity, and fill-timing metrics."""
        row = await self._db.get(DBTradeJournal, journal_id)
        if row is None:
            return
        row.fill_price = fill_price
        row.filled_quantity = filled_quantity
        if row.limit_price is not None:
            row.slippage = fill_price - row.limit_price
        now_et = datetime.now(tz=ET)
        row.filled_at = now_et
        if row.entry_time is not None:
            entry_et = (
                row.entry_time.replace(tzinfo=ET)
                if row.entry_time.tzinfo is None
                else row.entry_time.astimezone(ET)
            )
            row.time_to_fill_secs = max(0.0, (now_et - entry_et).total_seconds())
        logger.info(
            "Journal fill %d: filled=%d @ %.4f ttf=%.0fs",
            journal_id, filled_quantity, fill_price, row.time_to_fill_secs or 0,
        )

    # ── Reconciler recovery ───────────────────────────────────────────────────

    async def find_cancelled_for_reconciliation(
        self,
        option_symbol: str,
        session_date: str,
    ) -> Optional[Any]:
        """
        Find the most recent cancelled/stale_cancelled journal row for this
        option symbol and session date.  Used by the reconciler to restore the
        original strategy linkage when a broker position is discovered after a
        stale-cancel cycle.
        """
        from sqlalchemy import select, desc
        result = await self._db.execute(
            select(DBTradeJournal)
            .where(
                DBTradeJournal.option_symbol == option_symbol,
                DBTradeJournal.session_date == session_date,
                DBTradeJournal.status.in_(["cancelled"]),
            )
            .order_by(desc(DBTradeJournal.entry_time))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def find_open_for_symbol(
        self,
        option_symbol: str,
    ) -> Optional[Any]:
        """
        Find the most recent journal row for this option symbol still in
        'open' status, regardless of session_date.  Used at session startup
        to re-link a broker position that was carried over from a prior
        session (e.g. an EOD exit order that did not fill before the prior
        session ended) back to its original journal entry, instead of
        recording it as a new orphan position with no strategy/signal
        metadata.
        """
        from sqlalchemy import select, desc
        result = await self._db.execute(
            select(DBTradeJournal)
            .where(
                DBTradeJournal.option_symbol == option_symbol,
                DBTradeJournal.status == "open",
            )
            .order_by(desc(DBTradeJournal.entry_time))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def restore_reconciler_fill(
        self,
        journal_id: int,
        fill_price: float,
        filled_quantity: int,
        filled_at: datetime,
    ):
        """
        Update a cancelled journal row to reflect the actual broker fill that
        was discovered by the periodic reconciler.  Sets status back to 'open'
        so the normal exit path can close it when the position is eventually sold.
        """
        row = await self._db.get(DBTradeJournal, journal_id)
        if row is None:
            logger.warning("Journal record %d not found for reconciler restore", journal_id)
            return
        row.fill_price = fill_price
        row.filled_quantity = filled_quantity
        filled_at_et = filled_at.astimezone(ET) if filled_at.tzinfo else filled_at.replace(tzinfo=ET)
        row.filled_at = filled_at_et
        row.status = "open"
        row.exit_reason = None
        if row.limit_price is not None:
            row.slippage = fill_price - row.limit_price
        if row.entry_time is not None:
            entry_et = (
                row.entry_time.replace(tzinfo=ET)
                if row.entry_time.tzinfo is None
                else row.entry_time.astimezone(ET)
            )
            row.time_to_fill_secs = max(0.0, (filled_at_et - entry_et).total_seconds())
        logger.info(
            "Journal reconciler-fill restore %d: %s filled @ %.4f qty=%d",
            journal_id, row.option_symbol, fill_price, filled_quantity,
        )

    # ── Cancellation ──────────────────────────────────────────────────────────

    async def record_cancellation(
        self,
        journal_id: int,
        reason: str = "stale_order",
    ):
        """Mark an open entry as cancelled."""
        row = await self._db.get(DBTradeJournal, journal_id)
        if row is None:
            return
        row.status = "cancelled"
        row.exit_reason = reason
        logger.info("Journal cancellation %d: %s", journal_id, reason)

    # ── Rejection ─────────────────────────────────────────────────────────────

    async def record_rejection(
        self,
        *,
        strategy_id: str,
        signal_direction: str,
        underlying_symbol: str,
        underlying_price: float,
        option_symbol: Optional[str],
        rejection_reason: str,
        notes: Optional[str] = None,
        entry_time: Optional[datetime] = None,
    ) -> int:
        """Insert a rejected-signal record and return its row id."""
        now = entry_time or datetime.now(tz=ET)
        et = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
        row = DBTradeJournal(
            entry_time=now,
            session_date=et.strftime("%Y-%m-%d"),
            strategy_id=strategy_id,
            signal_direction=signal_direction,
            underlying_symbol=underlying_symbol,
            underlying_price=underlying_price,
            weekday=et.weekday(),
            option_symbol=option_symbol or "",
            rejection_reason=rejection_reason,
            status="rejected",
            is_paper=self._is_paper,
            notes=notes,
        )
        self._db.add(row)
        await self._db.flush()
        logger.info(
            "Journal rejection %d: %s — %s",
            row.id, underlying_symbol, rejection_reason[:80],
        )
        return row.id

    # ── Session log ───────────────────────────────────────────────────────────

    async def log_event(
        self,
        event: str,
        message: str,
        level: str = "info",
        symbol: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ):
        """Write a structured session log row."""
        now = datetime.now(tz=ET)
        row = DBSessionLog(
            session_date=now.strftime("%Y-%m-%d"),
            timestamp=now,
            level=level,
            event=event,
            symbol=symbol,
            message=message,
            data_json=json.dumps(data) if data else None,
        )
        self._db.add(row)

    # ── Commit ────────────────────────────────────────────────────────────────

    async def commit(self):
        await self._db.commit()
