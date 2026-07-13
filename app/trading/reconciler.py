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
from typing import List, Optional
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
    orders_confirmed: bool = False    # True iff broker.get_orders() succeeded
    positions_confirmed: bool = False  # True iff broker.get_positions() succeeded
    unresolved_orders: bool = False   # True when FillTracker entry absent from broker open orders

    @property
    def success(self) -> bool:
        """Both broker API calls succeeded — minimum precondition for clearing RECON_BLOCKED."""
        return self.orders_confirmed and self.positions_confirmed


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
        journal=None,
        risk=None,
        session_date: Optional[str] = None,
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
                    fill_price = float(bp.avg_cost)
                    filled_qty = abs(bp.quantity)

                    journal_id: Optional[int] = None
                    strategy_id = "reconciled"

                    # Try to find and restore the original cancelled journal row
                    if journal is not None and session_date is not None:
                        try:
                            original = await journal.find_cancelled_for_reconciliation(
                                option_symbol=opt_sym,
                                session_date=session_date,
                            )
                            if original is not None:
                                journal_id = original.id
                                strategy_id = original.strategy_id
                                await journal.restore_reconciler_fill(
                                    journal_id=original.id,
                                    fill_price=fill_price,
                                    filled_quantity=filled_qty,
                                    filled_at=now,
                                )
                                await journal.log_event(
                                    event="reconciler_fill_recovery",
                                    message=(
                                        f"Reconciler: restored fill for {opt_sym} "
                                        f"from cancelled journal row {original.id} "
                                        f"strategy={strategy_id} fill={fill_price:.4f}"
                                    ),
                                    level="info",
                                    symbol=bp.symbol,
                                    data={
                                        "journal_id": original.id,
                                        "strategy_id": strategy_id,
                                        "fill_price": fill_price,
                                        "filled_qty": filled_qty,
                                    },
                                )
                                await journal.commit()
                                logger.info(
                                    "Reconciler: restored cancelled journal row %d for %s "
                                    "strategy=%s fill=%.4f",
                                    original.id, opt_sym, strategy_id, fill_price,
                                )
                            else:
                                logger.warning(
                                    "Reconciler: no cancelled journal row for %s on %s "
                                    "— opening with strategy_id='reconciled' (no journal link)",
                                    opt_sym, session_date,
                                )
                        except Exception as _jexc:
                            logger.warning(
                                "Reconciler: journal lookup/restore failed for %s: %s "
                                "— proceeding without journal link",
                                opt_sym, _jexc,
                            )

                    pm.open(
                        option_symbol=opt_sym,
                        symbol=bp.symbol,
                        strategy_id=strategy_id,
                        direction=direction,
                        entry_time=now,
                        entry_price=fill_price,
                        quantity=filled_qty,
                        journal_id=journal_id,
                    )

                    if risk is not None:
                        risk.record_entry_filled()

                    msg = (
                        f"Reconciled: added broker position {opt_sym} "
                        f"qty={filled_qty} cost={fill_price:.4f}"
                    )
                    if journal_id is not None:
                        msg += f" journal_id={journal_id} strategy={strategy_id}"
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
                else:
                    # Both sides have the position — check quantity match
                    bp = broker_syms[pos.option_symbol]
                    broker_qty = abs(bp.quantity)
                    local_qty = abs(pos.quantity)
                    if broker_qty != local_qty:
                        msg = (
                            f"Quantity mismatch: {pos.option_symbol} "
                            f"broker_qty={broker_qty} local_qty={local_qty} — flagged for review"
                        )
                        logger.warning("Reconciler: %s", msg)
                        result.flagged.append(msg)

        except NotImplementedError:
            logger.debug("Reconciler: broker.get_positions() not available")
        except Exception as exc:
            logger.warning("Reconciler: position check failed: %s", exc)
        else:
            result.positions_confirmed = True

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

            broker_open_ids = {bo.order_id for bo in open_broker_orders}

            for bo in open_broker_orders:
                if bo.order_id not in ft_ids:
                    msg = (
                        f"Untracked broker order {bo.order_id[:8]} "
                        f"({bo.option_symbol}) status={bo.status.value}"
                    )
                    logger.warning("Reconciler: %s", msg)
                    result.flagged.append(msg)

            # FillTracker entries absent from broker open orders — status unresolved
            for ft_id in ft_ids:
                if ft_id not in broker_open_ids:
                    logger.info(
                        "Reconciler: FillTracker order %s absent from broker open orders "
                        "— awaiting next poll cycle",
                        ft_id[:8],
                    )
                    result.unresolved_orders = True

        except NotImplementedError:
            logger.debug("Reconciler: broker.get_orders() not available")
        except Exception as exc:
            logger.warning("Reconciler: order check failed: %s", exc)
        else:
            result.orders_confirmed = True

        # ── Update RECON_BLOCKED based on full reconciliation result ─────────
        # Clear only when ALL conditions are true:
        #   • both broker API calls succeeded (orders + positions)
        #   • no flagged discrepancies (unknown orders, qty mismatches, missing positions)
        #   • no FillTracker entries absent from broker open orders
        # Any failure actively re-sets the block to prevent a cleared flag from
        # persisting across a reconcile cycle that introduced new problems.
        if risk is not None:
            _clean = result.success and not result.flagged and not result.unresolved_orders
            if _clean:
                risk.clear_recon_blocked()
            else:
                _reasons: List[str] = []
                if not result.orders_confirmed:
                    _reasons.append("broker.get_orders() failed")
                if not result.positions_confirmed:
                    _reasons.append("broker.get_positions() failed")
                if result.flagged:
                    _reasons.append(f"{len(result.flagged)} reconciliation flag(s)")
                if result.unresolved_orders:
                    _reasons.append("FillTracker has orders absent from broker")
                risk.set_recon_blocked("; ".join(_reasons) if _reasons else "reconciliation incomplete")

        logger.info(
            "Reconciliation done: broker_pos=%d local_pos=%d "
            "broker_orders=%d local_pending=%d repaired=%d flagged=%d "
            "orders_confirmed=%s positions_confirmed=%s",
            result.broker_positions, result.local_positions,
            result.broker_open_orders, result.local_pending_orders,
            len(result.repaired), len(result.flagged),
            result.orders_confirmed, result.positions_confirmed,
        )
        return result
