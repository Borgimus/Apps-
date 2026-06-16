"""
Session recovery — rebuild in-memory state from the database and broker
after a crash or restart so the runner resumes cleanly.

Recovery sequence
─────────────────
1. Load today's non-terminal DBPendingOrder rows → re-register each in
   FillTracker.  The very next poll() cycle will query their current
   status from the broker and process fills / cancellations normally.

2. Fetch broker option positions → add any that PositionManager does not
   yet know about.  This handles the case where the fill was processed
   (PM was opened) but the runner then crashed before persisting PM state.

3. Cross-check: PM positions the broker does not have → warn.  These may
   be positions from a previous session or from a fill that was not yet
   propagated.  We log them but do NOT remove them automatically.

4. Cross-check: broker open orders not tracked by FillTracker → warn.
   These represent a potential duplicate-order risk; the operator should
   investigate before re-starting.

Duplicate prevention
────────────────────
After recovery, FillTracker.has_pending_for_symbol() and
PositionManager.has_position_for_symbol() will both return True for any
symbol that is already being tracked, so scan_and_place() will skip
fresh signals for those symbols — no duplicate orders.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List
from zoneinfo import ZoneInfo

from .fill_tracker import FillTracker
from .pending_order_store import PendingOrderStore
from .position_manager import PositionManager

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


@dataclass
class RecoveryResult:
    pending_orders_loaded: int = 0
    broker_positions_loaded: int = 0
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class SessionRecovery:
    """
    Rebuild FillTracker and PositionManager state from DB + broker data.

    Usage::

        recovery = SessionRecovery()
        result = await recovery.recover(broker, pm, fill_tracker, store, "2024-01-15")
        for w in result.warnings:
            logger.warning("Recovery: %s", w)
    """

    async def recover(
        self,
        broker,
        pm: PositionManager,
        fill_tracker: FillTracker,
        store: PendingOrderStore,
        session_date: str,
        journal=None,
    ) -> RecoveryResult:
        result = RecoveryResult()
        now = datetime.now(tz=ET)

        # ── Step 1: Reload pending orders from DB ─────────────────────────────
        try:
            open_rows = await store.load_open_for_session(session_date)
            for row in open_rows:
                if row.order_id in fill_tracker._pending:
                    continue  # already tracked (shouldn't happen on fresh start)
                fill_tracker.register(
                    order_id=row.order_id,
                    journal_id=row.journal_id or 0,
                    option_symbol=row.option_symbol,
                    symbol=row.symbol,
                    strategy_id=row.strategy_id or "",
                    direction=row.direction or "LONG",
                    quantity=row.quantity,
                    limit_price=row.limit_price,
                    placed_at=row.submitted_at or now,
                )
                result.pending_orders_loaded += 1
                logger.info(
                    "Recovery: re-registered %s (%s)",
                    row.order_id[:8], row.option_symbol,
                )
        except Exception as exc:
            msg = f"Failed to load pending orders from DB: {exc}"
            logger.error("Recovery: %s", msg)
            result.errors.append(msg)

        # ── Step 2: Fetch broker positions → populate PM ───────────────────
        broker_syms: set = set()
        try:
            broker_positions = await broker.get_positions()
            option_positions = [p for p in broker_positions if p.is_option and p.option_symbol]
            for bp in option_positions:
                broker_syms.add(bp.option_symbol)
                if pm.has_position(bp.option_symbol):
                    continue
                direction = "LONG" if bp.quantity > 0 else "SHORT"

                # Carried-over position from a prior session (e.g. an EOD
                # exit order that did not fill before the prior session
                # ended): re-link to its original journal row instead of
                # recording it as an orphan with no strategy/signal metadata.
                journal_id = None
                strategy_id = "recovered"
                entry_time = now
                if journal is not None:
                    try:
                        original = await journal.find_open_for_symbol(bp.option_symbol)
                        if original is not None:
                            journal_id = original.id
                            strategy_id = original.strategy_id
                            if original.entry_time is not None:
                                entry_time = (
                                    original.entry_time.replace(tzinfo=ET)
                                    if original.entry_time.tzinfo is None
                                    else original.entry_time.astimezone(ET)
                                )
                            logger.info(
                                "Recovery: re-linked carryover position %s to "
                                "journal row %d (session_date=%s) strategy=%s "
                                "original_entry=%s",
                                bp.option_symbol, original.id,
                                original.session_date, strategy_id, entry_time,
                            )
                    except Exception as _jexc:
                        logger.warning(
                            "Recovery: journal lookup failed for %s: %s "
                            "— proceeding without journal link",
                            bp.option_symbol, _jexc,
                        )

                pm.open(
                    option_symbol=bp.option_symbol,
                    symbol=bp.symbol,
                    strategy_id=strategy_id,
                    direction=direction,
                    entry_time=entry_time,
                    entry_price=float(bp.avg_cost),
                    quantity=abs(bp.quantity),
                    journal_id=journal_id,
                )
                result.broker_positions_loaded += 1
                logger.info(
                    "Recovery: loaded broker position %s qty=%d cost=%.4f journal_id=%s",
                    bp.option_symbol, abs(bp.quantity), float(bp.avg_cost), journal_id,
                )
        except NotImplementedError:
            msg = "Broker does not support get_positions() — broker positions not recovered"
            logger.warning("Recovery: %s", msg)
            result.warnings.append(msg)
        except Exception as exc:
            msg = f"Failed to fetch broker positions: {exc}"
            logger.error("Recovery: %s", msg)
            result.errors.append(msg)

        # ── Step 3: PM positions the broker doesn't recognise ──────────────
        if broker_syms:
            for pos in pm.open_positions():
                if pos.strategy_id in ("recovered", "reconciled"):
                    continue
                if pos.option_symbol not in broker_syms:
                    msg = (
                        f"PM has {pos.option_symbol} but broker does not "
                        f"— may be a stale position from a previous session"
                    )
                    logger.warning("Recovery: %s", msg)
                    result.warnings.append(msg)

        # ── Step 4: Broker open orders not in FillTracker ─────────────────
        try:
            from ..brokers.broker_interface import OrderStatus
            broker_orders = await broker.get_orders(limit=200)
            _working = {
                OrderStatus.NEW, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED,
                OrderStatus.ACCEPTED, OrderStatus.PENDING_NEW, OrderStatus.HELD,
            }
            ft_ids = set(fill_tracker._pending.keys())
            for bo in broker_orders:
                if bo.status in _working and bo.order_id not in ft_ids:
                    msg = (
                        f"Broker has open order {bo.order_id[:8]} "
                        f"({bo.option_symbol}) not in FillTracker — "
                        f"possible untracked order; investigate before re-placing"
                    )
                    logger.warning("Recovery: %s", msg)
                    result.warnings.append(msg)
        except NotImplementedError:
            logger.debug("Recovery: broker.get_orders() not available — skipping")
        except Exception as exc:
            msg = f"Could not check broker open orders: {exc}"
            logger.warning("Recovery: %s", msg)
            result.warnings.append(msg)

        logger.info(
            "Recovery complete: %d pending orders, %d broker positions, "
            "%d warnings, %d errors",
            result.pending_orders_loaded,
            result.broker_positions_loaded,
            len(result.warnings),
            len(result.errors),
        )
        return result
