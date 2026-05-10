"""
Broker reconciliation — periodic comparison of local state to broker state.

Reconciliation checks (broker is authoritative for positions)
─────────────────────────────────────────────────────────────
1. Broker position not in PM  → add to PM and log (repaired).
2. PM position not at broker  → flag for manual review; do NOT auto-remove
   because the fill may still be in transit.
3. Broker open order not in FillTracker → flag (untracked order risk).
4. FillTracker order the broker calls dead → handled automatically on the
   next fill_tracker.poll() cycle; reconciler does not duplicate that work.

Calling convention
──────────────────
Call reconcile() once after startup recovery and then every
reconcile_interval_minutes.  Results accumulate in the session runner's
recon_warnings list for inclusion in the final health report.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List
from zoneinfo import ZoneInfo

from .fill_tracker import FillTracker
from .position_manager import PositionManager

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


@dataclass
class ReconciliationResult:
    broker_positions: int = 0
    local_positions: int = 0
    broker_open_orders: int = 0
    local_pending_orders: int = 0
    repaired: List[str] = field(default_factory=list)
    flagged: List[str] = field(default_factory=list)


class Reconciler:
    """
    Compare local PM / FillTracker state to live broker state and
    repair discrepancies where the broker is authoritative.
    """

    async def reconcile(
        self,
        broker,
        pm: PositionManager,
        fill_tracker: FillTracker,
        now: datetime,
    ) -> ReconciliationResult:
        result = ReconciliationResult(
            local_positions=len(pm.open_positions()),
            local_pending_orders=fill_tracker.count(),
        )

        # ── Position reconciliation ───────────────────────────────────────────
        try:
            broker_positions = await broker.get_positions()
            option_positions = [p for p in broker_positions if p.is_option and p.option_symbol]
            result.broker_positions = len(option_positions)
            broker_syms = {p.option_symbol: p for p in option_positions}

            # Broker has position, PM doesn't — add it
            for opt_sym, bp in broker_syms.items():
                if not pm.has_position(opt_sym):
                    direction = "LONG" if bp.quantity > 0 else "SHORT"
                    pm.open(
                        option_symbol=opt_sym,
                        symbol=bp.symbol,
                        strategy_id="reconciled",
                        direction=direction,
                        entry_time=now,
                        entry_price=float(bp.avg_cost),
                        quantity=abs(bp.quantity),
                    )
                    msg = (
                        f"Reconciled: added broker position {opt_sym} "
                        f"qty={abs(bp.quantity)} cost={float(bp.avg_cost):.4f}"
                    )
                    logger.warning("Reconciler: %s", msg)
                    result.repaired.append(msg)

            # PM has position, broker doesn't — flag only
            for pos in pm.open_positions():
                if pos.strategy_id in ("reconciled", "recovered"):
                    continue
                if pos.option_symbol not in broker_syms:
                    msg = (
                        f"Mismatch: PM has {pos.option_symbol} "
                        f"but broker has no such position — flagged for review"
                    )
                    logger.warning("Reconciler: %s", msg)
                    result.flagged.append(msg)

        except NotImplementedError:
            logger.debug("Reconciler: broker.get_positions() not available")
        except Exception as exc:
            logger.warning("Reconciler: position check failed: %s", exc)

        # ── Order reconciliation ──────────────────────────────────────────────
        try:
            from ..brokers.broker_interface import OrderStatus
            broker_orders = await broker.get_orders(limit=200)
            _working = {
                OrderStatus.NEW, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED,
                OrderStatus.ACCEPTED, OrderStatus.PENDING_NEW, OrderStatus.HELD,
            }
            open_broker_orders = [o for o in broker_orders if o.status in _working]
            result.broker_open_orders = len(open_broker_orders)
            ft_ids = set(fill_tracker._pending.keys())

            for bo in open_broker_orders:
                if bo.order_id not in ft_ids:
                    msg = (
                        f"Untracked broker order {bo.order_id[:8]} "
                        f"({bo.option_symbol}) status={bo.status.value}"
                    )
                    logger.warning("Reconciler: %s", msg)
                    result.flagged.append(msg)

        except NotImplementedError:
            logger.debug("Reconciler: broker.get_orders() not available")
        except Exception as exc:
            logger.warning("Reconciler: order check failed: %s", exc)

        logger.info(
            "Reconciliation done: broker_pos=%d local_pos=%d "
            "broker_orders=%d local_pending=%d repaired=%d flagged=%d",
            result.broker_positions, result.local_positions,
            result.broker_open_orders, result.local_pending_orders,
            len(result.repaired), len(result.flagged),
        )
        return result
