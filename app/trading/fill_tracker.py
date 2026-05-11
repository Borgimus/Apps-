"""
Fill tracker — monitors pending Alpaca orders and updates the position manager
and trade journal when fills (full or partial) or cancellations arrive.

Why this matters
────────────────
Limit orders placed on Alpaca do not fill synchronously.  The session runner
places a limit order and moves on.  Without fill tracking:
  • The position manager holds the *ask* price as the entry price, which is
    wrong whenever the order fills at a better price (or doesn't fill at all).
  • Cancelled / expired orders leave phantom positions open in PM, blocking
    new signals for that symbol all day (dedup false-positive).
  • Journal entries stay "open" forever even when the broker rejects the order.

How it works
────────────
1. After placing an order, call fill_tracker.register(...)
2. On each poll cycle, call await fill_tracker.poll(broker, pm, journal, now)
3. poll() calls broker.get_order_status() for every pending order:
     FILLED        → record_fill() in journal; update PM entry price; log
     PARTIALLY_FILLED → record_fill() in journal (partial); keep polling
     CANCELLED / REJECTED / EXPIRED
                   → record_cancellation() in journal; remove phantom PM position
     All others (NEW, ACCEPTED, …) → keep waiting
4. Stale orders older than max_age_minutes are cancelled and removed.

Dedup integration
─────────────────
Call fill_tracker.has_pending_for_symbol(symbol) alongside
pm.has_position_for_symbol(symbol) before scanning for new signals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from ..brokers.broker_interface import OrderStatus
from ..utils.alerting import AlertEvent, AlertService

if TYPE_CHECKING:
    from .pending_order_store import PendingOrderStore

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

# Statuses that mean "still working"
_PENDING_STATUSES = {
    OrderStatus.PENDING,
    OrderStatus.ACCEPTED,
    OrderStatus.PENDING_NEW,
    OrderStatus.NEW,
    OrderStatus.OPEN,
    OrderStatus.HELD,
}

# Statuses that mean "done without fill"
_DEAD_STATUSES = {
    OrderStatus.CANCELLED,
    OrderStatus.CANCELED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
}


@dataclass
class PendingOrder:
    order_id: str
    journal_id: int
    option_symbol: str
    symbol: str           # underlying
    strategy_id: str
    direction: str
    quantity: int
    limit_price: float
    placed_at: datetime


class FillTracker:
    """
    In-memory registry of orders awaiting fill confirmation.

    All methods are synchronous except poll(), which calls the broker.
    """

    def __init__(
        self,
        max_age_minutes: int = 30,
        store: Optional[Any] = None,
        alert_service: Optional[AlertService] = None,
    ):
        """
        Parameters
        ----------
        max_age_minutes : int
            Orders older than this are cancelled on the next poll cycle.
        store : PendingOrderStore | None
            When supplied, fill / dead events also update the DB row so
            status is durable across restarts.  The store must share the
            same AsyncSession as the TradeJournal passed to poll() so
            that both updates commit atomically.
        alert_service : AlertService | None
            When supplied, sends alerts on fill and cancel events.
        """
        self._pending: Dict[str, PendingOrder] = {}   # order_id → PendingOrder
        self._max_age_minutes = max_age_minutes
        self._store: Optional[Any] = store
        self._alerts: Optional[AlertService] = alert_service
        self._last_status: Dict[str, str] = {}        # order_id → last known status string

    # ── Registration ──────────────────────────────────────────────────────────

    def register(
        self,
        order_id: str,
        journal_id: int,
        option_symbol: str,
        symbol: str,
        strategy_id: str,
        direction: str,
        quantity: int,
        limit_price: float,
        placed_at: Optional[datetime] = None,
    ) -> PendingOrder:
        po = PendingOrder(
            order_id=order_id,
            journal_id=journal_id,
            option_symbol=option_symbol,
            symbol=symbol,
            strategy_id=strategy_id,
            direction=direction,
            quantity=quantity,
            limit_price=limit_price,
            placed_at=placed_at or datetime.now(tz=ET),
        )
        self._pending[order_id] = po
        logger.info(
            "FillTracker registered %s | %s | limit=%.4f",
            order_id[:8], option_symbol, limit_price,
        )
        return po

    # ── Query ─────────────────────────────────────────────────────────────────

    def has_pending_for_symbol(self, symbol: str) -> bool:
        return any(p.symbol == symbol for p in self._pending.values())

    def pending_orders(self) -> List[PendingOrder]:
        return list(self._pending.values())

    def count(self) -> int:
        return len(self._pending)

    # ── Poll cycle ────────────────────────────────────────────────────────────

    async def poll(
        self,
        broker,
        pm,
        journal,
        now: datetime,
        risk=None,
    ) -> int:
        """
        Check every pending order against the broker.

        Returns the number of fills processed (full or partial).
        """
        if not self._pending:
            return 0

        fills = 0
        for order_id, pending in list(self._pending.items()):

            # ── Stale-order cancellation ──────────────────────────────────────
            age_min = (now - pending.placed_at).total_seconds() / 60
            if age_min > self._max_age_minutes:
                logger.warning(
                    "FillTracker: stale order %s (%.0f min) — cancelling",
                    order_id[:8], age_min,
                )
                try:
                    await broker.cancel_order(order_id)
                except Exception as exc:
                    logger.warning("Cancel failed for %s: %s", order_id[:8], exc)
                await self._handle_dead(order_id, pending, "stale_cancelled", pm, journal)
                continue

            # ── Fetch latest status from broker ───────────────────────────────
            try:
                order = await broker.get_order_status(order_id)
            except Exception as exc:
                logger.warning(
                    "FillTracker: cannot fetch status for %s: %s — will retry",
                    order_id[:8], exc,
                )
                continue

            status = order.status
            status_str = status.value
            prev_status = self._last_status.get(order_id, "pending")

            # Record telemetry on every status change
            if status_str != prev_status:
                await self._record_transition(
                    order_id=order_id,
                    pending=pending,
                    prev_status=prev_status,
                    new_status=status_str,
                    filled_qty=order.filled_quantity or 0,
                    avg_fill_price=float(order.filled_price) if order.filled_price else None,
                    journal=journal,
                    now=now,
                )
                self._last_status[order_id] = status_str

            if status == OrderStatus.FILLED:
                fills += await self._handle_fill(
                    order_id, pending, order, pm, journal, now, risk, partial=False
                )

            elif status == OrderStatus.PARTIALLY_FILLED:
                fills += await self._handle_fill(
                    order_id, pending, order, pm, journal, now, risk, partial=True
                )

            elif status in _DEAD_STATUSES:
                logger.info(
                    "FillTracker: order %s %s — removing",
                    order_id[:8], status.value,
                )
                await self._handle_dead(order_id, pending, status.value, pm, journal)

            else:
                # Still pending (NEW, ACCEPTED, etc.)
                logger.debug(
                    "FillTracker: %s still %s (%.0f min old)",
                    order_id[:8], status.value, age_min,
                )

        return fills

    # ── Internals ─────────────────────────────────────────────────────────────

    async def _handle_fill(
        self,
        order_id: str,
        pending: PendingOrder,
        order,
        pm,
        journal,
        now: datetime,
        risk,
        partial: bool,
    ) -> int:
        fill_price = float(order.filled_price) if order.filled_price else pending.limit_price
        filled_qty = order.filled_quantity or 0

        logger.info(
            "FillTracker: %s %s @ %.4f (%d/%d contracts)",
            "PARTIAL" if partial else "FILL",
            order_id[:8], fill_price, filled_qty, pending.quantity,
        )

        # Update journal + persistent store
        if journal and pending.journal_id:
            await journal.record_fill(
                journal_id=pending.journal_id,
                fill_price=fill_price,
                filled_quantity=filled_qty,
            )
            await journal.log_event(
                event="fill",
                message=(
                    f"{'Partial fill' if partial else 'Fill'}: "
                    f"{pending.option_symbol} {filled_qty}×@ {fill_price:.4f}"
                ),
                level="info",
                symbol=pending.symbol,
                data={
                    "order_id": order_id,
                    "fill_price": fill_price,
                    "filled_qty": filled_qty,
                    "partial": partial,
                },
            )
            if self._store:
                db_status = "partially_filled" if partial else "filled"
                await self._store.update_status(
                    order_id, db_status, filled_qty, fill_price
                )
            await journal.commit()
        elif self._store:
            db_status = "partially_filled" if partial else "filled"
            await self._store.update_status(order_id, db_status, filled_qty, fill_price)
            await self._store.commit()

        # Send alert
        if self._alerts:
            event = AlertEvent.ORDER_PARTIAL if partial else AlertEvent.ORDER_FILLED
            await self._alerts.send(
                event,
                f"{pending.option_symbol} {filled_qty}×@ {fill_price:.4f}",
                data={
                    "order_id": order_id,
                    "symbol": pending.symbol,
                    "strategy": pending.strategy_id,
                    "fill_price": fill_price,
                    "filled_qty": filled_qty,
                    "total_qty": pending.quantity,
                },
            )

        # Open / update position with real fill price
        if not partial:
            # Full fill: open position in PM (entry_price = actual fill)
            if not pm.has_position(pending.option_symbol):
                pm.open(
                    option_symbol=pending.option_symbol,
                    symbol=pending.symbol,
                    strategy_id=pending.strategy_id,
                    direction=pending.direction,
                    entry_time=now,
                    entry_price=fill_price,
                    quantity=filled_qty or pending.quantity,
                    journal_id=pending.journal_id,
                )
            else:
                # Already opened via a prior partial fill — update entry price to final fill price
                pos = pm._positions.get(pending.option_symbol)
                if pos is not None:
                    pos.entry_price = fill_price
                logger.warning("FillTracker: position already open for %s — updated entry price", pending.option_symbol)

            del self._pending[order_id]
            self._last_status.pop(order_id, None)
            return 1

        else:
            # Partial: keep in pending until fully filled or cancelled.
            # Open position for the filled portion so PM can manage it.
            if filled_qty > 0 and not pm.has_position(pending.option_symbol):
                pm.open(
                    option_symbol=pending.option_symbol,
                    symbol=pending.symbol,
                    strategy_id=pending.strategy_id,
                    direction=pending.direction,
                    entry_time=now,
                    entry_price=fill_price,
                    quantity=filled_qty,
                    journal_id=pending.journal_id,
                )
            return 1

    async def _handle_dead(
        self,
        order_id: str,
        pending: PendingOrder,
        reason: str,
        pm,
        journal,
    ):
        """Handle cancelled / rejected / expired / stale order."""
        # Remove phantom position if it was opened optimistically before fill confirmation
        if pm.has_position(pending.option_symbol):
            logger.warning(
                "FillTracker: removing phantom position %s (order %s)",
                pending.option_symbol, reason,
            )
            pm._positions.pop(pending.option_symbol, None)

        if journal and pending.journal_id:
            await journal.record_cancellation(
                journal_id=pending.journal_id,
                reason=reason,
            )
            await journal.log_event(
                event="cancel",
                message=f"Order {order_id[:8]} {reason}: {pending.option_symbol}",
                level="warning",
                symbol=pending.symbol,
                data={"order_id": order_id, "reason": reason},
            )
            if self._store:
                db_status = (
                    "cancelled"
                    if reason in ("stale_cancelled", "cancelled", "canceled")
                    else reason
                )
                await self._store.update_status(order_id, db_status)
            await journal.commit()
        elif self._store:
            db_status = (
                "cancelled"
                if reason in ("stale_cancelled", "cancelled", "canceled")
                else reason
            )
            await self._store.update_status(order_id, db_status)
            await self._store.commit()

        # Send alert
        if self._alerts:
            if reason == "stale_cancelled":
                event = AlertEvent.ORDER_STALE_CANCELLED
            elif reason in ("cancelled", "canceled"):
                event = AlertEvent.ORDER_CANCELLED
            elif reason == "rejected":
                event = AlertEvent.ORDER_REJECTED
            else:
                event = AlertEvent.ORDER_CANCELLED
            await self._alerts.send(
                event,
                f"Order {order_id[:8]} {reason}: {pending.option_symbol}",
                data={
                    "order_id": order_id,
                    "symbol": pending.symbol,
                    "strategy": pending.strategy_id,
                    "reason": reason,
                },
            )

        del self._pending[order_id]
        self._last_status.pop(order_id, None)

    async def _record_transition(
        self,
        order_id: str,
        pending: PendingOrder,
        prev_status: str,
        new_status: str,
        filled_qty: int,
        avg_fill_price: Optional[float],
        journal,
        now: datetime,
    ):
        """Persist a single order status transition to the telemetry table."""
        if journal is None:
            return
        try:
            from ..api.models import DBOrderStatusTransition
            row = DBOrderStatusTransition(
                order_id=order_id,
                journal_id=pending.journal_id,
                option_symbol=pending.option_symbol,
                symbol=pending.symbol,
                prev_status=prev_status,
                status=new_status,
                filled_qty=filled_qty,
                avg_fill_price=avg_fill_price,
                timestamp=now,
            )
            journal._db.add(row)
            logger.debug(
                "Telemetry: %s %s→%s qty=%d price=%s",
                order_id[:8], prev_status, new_status,
                filled_qty, f"{avg_fill_price:.4f}" if avg_fill_price else "—",
            )
        except Exception as exc:
            logger.debug("Telemetry write failed (non-fatal): %s", exc)
