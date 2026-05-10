"""
Persistent store for pending broker orders.

PendingOrderStore wraps the DBPendingOrder table so the session can
survive crashes: on startup, open rows are reloaded into FillTracker;
on every status change FillTracker calls update_status() so the DB
always reflects the latest broker-reported state.

All methods operate on the caller-supplied AsyncSession.  Because
TradeJournal shares the same session, a single journal.commit() call
persists both journal updates and pending-order status changes atomically.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.models import DBPendingOrder
from .fill_tracker import PendingOrder

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

# Statuses that mean the order is still in play
_OPEN_STATUSES = {"pending", "partially_filled"}


class PendingOrderStore:
    """Thin async CRUD layer over DBPendingOrder."""

    def __init__(self, db: AsyncSession):
        self._db = db

    # ── Write ──────────────────────────────────────────────────────────────────

    async def save(self, pending: PendingOrder, session_date: str) -> None:
        """Persist a newly registered pending order (status='pending')."""
        row = DBPendingOrder(
            order_id=pending.order_id,
            journal_id=pending.journal_id,
            option_symbol=pending.option_symbol,
            symbol=pending.symbol,
            strategy_id=pending.strategy_id,
            direction=pending.direction,
            quantity=pending.quantity,
            limit_price=pending.limit_price,
            submitted_at=pending.placed_at,
            status="pending",
            filled_quantity=0,
            session_date=session_date,
        )
        self._db.add(row)
        logger.debug("PendingOrderStore: saved %s", pending.order_id[:8])

    async def update_status(
        self,
        order_id: str,
        status: str,
        filled_quantity: int = 0,
        avg_fill_price: Optional[float] = None,
        last_polled_at: Optional[datetime] = None,
    ) -> None:
        """Update status and fill details for an existing row."""
        result = await self._db.execute(
            select(DBPendingOrder).where(DBPendingOrder.order_id == order_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            logger.warning("PendingOrderStore: row not found for order %s", order_id[:8])
            return
        row.status = status
        if filled_quantity:
            row.filled_quantity = filled_quantity
        if avg_fill_price is not None:
            row.avg_fill_price = avg_fill_price
        row.last_polled_at = last_polled_at or datetime.now(tz=ET)
        logger.debug(
            "PendingOrderStore: updated %s → %s (filled=%d)",
            order_id[:8], status, filled_quantity,
        )

    # ── Read ───────────────────────────────────────────────────────────────────

    async def load_open_for_session(self, session_date: str) -> List[DBPendingOrder]:
        """Return all non-terminal pending orders for a session date."""
        result = await self._db.execute(
            select(DBPendingOrder)
            .where(DBPendingOrder.session_date == session_date)
            .where(DBPendingOrder.status.in_(list(_OPEN_STATUSES)))
            .order_by(DBPendingOrder.submitted_at)
        )
        return list(result.scalars().all())

    async def load_all_for_session(self, session_date: str) -> List[DBPendingOrder]:
        """Return every pending-order row for a session date (any status)."""
        result = await self._db.execute(
            select(DBPendingOrder)
            .where(DBPendingOrder.session_date == session_date)
            .order_by(DBPendingOrder.submitted_at)
        )
        return list(result.scalars().all())

    async def has_order_id(self, order_id: str) -> bool:
        """Return True if this order_id already exists in the table."""
        result = await self._db.execute(
            select(DBPendingOrder.id).where(DBPendingOrder.order_id == order_id)
        )
        return result.scalar_one_or_none() is not None

    # ── Commit ─────────────────────────────────────────────────────────────────

    async def commit(self) -> None:
        await self._db.commit()
