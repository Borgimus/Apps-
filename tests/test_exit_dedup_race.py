"""
Tests for the exit order deduplication gate and the cancel-fill race condition.

The critical invariant: the total quantity submitted to the broker for a position
must never exceed the broker-held quantity.  A cancel-fill race occurs when:

  1. An exit order is outstanding.
  2. A cancel is requested (e.g. bid moved materially, or stale reprice).
  3. A fill arrives at the exchange before the cancel is processed.
  4. The cancel confirmation comes back with status=CANCELLED (or CANCELED) but
     filled_quantity > 0.

If the runner submits a replacement order without accounting for those fills it
will send a total of (original fill + replacement) sells against a (1-contract)
position, producing an unintended short.

Scenarios covered:
  A. _poll_pending_exit — reprice path: cancel confirms with filled_quantity=1
     → no replacement placed; next poll closes via fully_filled=True.
  B. _poll_pending_exit — reprice path: cancel confirms with filled_quantity=0
     → replacement IS placed for full remaining qty.
  C. _poll_pending_exit — terminal path: status=CANCELLED, filled_quantity=1
     → treated as fully_filled; no replacement; position closed.
  D. _place_exit_order — guard: remaining <= 0 → nothing placed.
  E. EOD reprice — cancel confirms with filled_quantity=1 → no replacement.
  F. Mandatory exit gate: should_exit() returns None when exit_pending=True.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.brokers.broker_interface import OrderResult, OrderStatus
from app.trading.position_manager import OpenPosition, PositionManager


ET_NAIVE = datetime(2026, 7, 14, 11, 0, 0)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_settings():
    s = MagicMock()
    s.position.stop_loss_pct = 0.50
    s.position.take_profit_pct = 1.00
    s.position.trailing_stop_pct = 0.25
    s.position.max_hold_minutes = 120
    s.position.eod_exit_time = "15:45"
    s.position.cooldown_after_loss_minutes = 15
    return s


def _make_pm_with_exit_pending(
    option_symbol="SPY240714C00450000",
    qty=1,
    entry_price=2.00,
    confirmed_fill_qty=0,
    exit_order_id="ORD-EXIT-001",
):
    pm = PositionManager(_make_settings())
    pm.open(
        option_symbol=option_symbol,
        symbol="SPY",
        strategy_id="orb",
        direction="LONG",
        entry_time=ET_NAIVE,
        entry_price=entry_price,
        quantity=qty,
    )
    pm.mark_exit_pending(
        option_symbol=option_symbol,
        order_id=exit_order_id,
        limit_price=1.80,
        reason="stop_loss",
        is_mandatory=True,
        now=ET_NAIVE,
    )
    pos = pm.get_position(option_symbol)
    pos.confirmed_fill_qty = confirmed_fill_qty
    pos.confirmed_fill_value = confirmed_fill_qty * 1.80
    return pm


def _order_result(status: OrderStatus, filled_qty: int = 0, filled_price: float = 0.0,
                  order_id: str = "ORD-EXIT-001"):
    r = MagicMock(spec=OrderResult)
    r.order_id = order_id
    r.status = status
    r.filled_quantity = filled_qty
    r.filled_price = Decimal(str(filled_price)) if filled_price else None
    r.filled_at = None
    return r


def _make_broker(order_status_result, cancel_result=True):
    broker = MagicMock()
    broker.get_order_status = AsyncMock(return_value=order_status_result)
    broker.cancel_order = AsyncMock(return_value=cancel_result)
    broker.place_option_order = AsyncMock()
    broker.get_option_quote = AsyncMock(return_value=MagicMock(bid=Decimal("1.20"), ask=Decimal("1.40"), mid=Decimal("1.30")))
    return broker


def _make_risk():
    risk = MagicMock()
    risk.record_exit = MagicMock()
    return risk


# ═══════════════════════════════════════════════════════════════════════════════
# A. Reprice cancel-fill race: cancel confirms CANCELLED but filled_qty=1
# ═══════════════════════════════════════════════════════════════════════════════

class TestRepriceCancelFillRace:

    @pytest.mark.asyncio
    async def test_no_replacement_when_cancel_confirms_with_fill(self):
        """
        Bid moves → cancel requested → cancel confirms CANCELLED with filled_qty=1.
        No replacement should be placed.  Position defers close to next poll cycle.
        """
        from scripts.session_runner import _poll_pending_exit

        pm = _make_pm_with_exit_pending(qty=1, entry_price=2.00)
        pos = pm.get_position("SPY240714C00450000")
        pos.exit_order_limit_price = 2.50  # current limit; bid will be below this

        # First get_order_status call (order still open) — triggers reprice path
        still_open = _order_result(OrderStatus.OPEN, filled_qty=0)
        # Second call (cancel confirmation) — returns CANCELLED but with a fill
        cancel_confirmed_with_fill = _order_result(OrderStatus.CANCELLED, filled_qty=1, filled_price=1.80)

        broker = MagicMock()
        broker.cancel_order = AsyncMock(return_value=True)
        broker.place_option_order = AsyncMock()
        # First call: still open; second call: cancelled with fill
        broker.get_order_status = AsyncMock(side_effect=[still_open, cancel_confirmed_with_fill])
        # Quote shows bid has fallen below limit
        broker.get_option_quote = AsyncMock(return_value=MagicMock(
            bid=Decimal("1.20"), ask=Decimal("1.40"), mid=Decimal("1.30")
        ))

        risk = _make_risk()
        result = await _poll_pending_exit(pos, broker, pm, None, risk, ET_NAIVE)

        # No replacement order placed
        broker.place_option_order.assert_not_called()
        # Position still in PM (will be closed on next poll when fully_filled=True)
        assert pm.has_position("SPY240714C00450000")
        # confirmed_fill_qty should be updated to 1
        pos_after = pm.get_position("SPY240714C00450000")
        assert pos_after is not None
        assert pos_after.confirmed_fill_qty == 1

    @pytest.mark.asyncio
    async def test_replacement_placed_when_cancel_confirms_no_fill(self):
        """
        Bid moves → cancel confirmed CANCELLED with filled_qty=0.
        Replacement IS placed for the full remaining quantity.
        """
        from scripts.session_runner import _poll_pending_exit

        pm = _make_pm_with_exit_pending(qty=1, entry_price=2.00)
        pos = pm.get_position("SPY240714C00450000")
        pos.exit_order_limit_price = 2.50

        still_open = _order_result(OrderStatus.OPEN, filled_qty=0)
        cancel_clean = _order_result(OrderStatus.CANCELLED, filled_qty=0)

        new_order = MagicMock()
        new_order.order_id = "ORD-EXIT-002"

        broker = MagicMock()
        broker.cancel_order = AsyncMock(return_value=True)
        broker.place_option_order = AsyncMock(return_value=new_order)
        broker.get_order_status = AsyncMock(side_effect=[still_open, cancel_clean])
        broker.get_option_quote = AsyncMock(return_value=MagicMock(
            bid=Decimal("1.20"), ask=Decimal("1.40"), mid=Decimal("1.30")
        ))

        risk = _make_risk()
        await _poll_pending_exit(pos, broker, pm, None, risk, ET_NAIVE)

        # Replacement order placed for qty=1
        broker.place_option_order.assert_called_once()
        call_args = broker.place_option_order.call_args[0][0]
        assert call_args.quantity == 1


# ═══════════════════════════════════════════════════════════════════════════════
# B. Terminal path: CANCELLED with filled_qty=1 → close, not re-place
# ═══════════════════════════════════════════════════════════════════════════════

class TestTerminalWithFill:

    @pytest.mark.asyncio
    async def test_cancelled_with_fill_closes_position(self):
        """
        Order status comes back CANCELLED but filled_quantity=1 (full fill).
        Position should be closed; no replacement order placed.
        """
        from scripts.session_runner import _poll_pending_exit

        pm = _make_pm_with_exit_pending(qty=1, entry_price=2.00)
        pos = pm.get_position("SPY240714C00450000")

        cancelled_with_fill = _order_result(OrderStatus.CANCELLED, filled_qty=1, filled_price=1.80)
        broker = _make_broker(cancelled_with_fill)

        risk = _make_risk()
        result = await _poll_pending_exit(pos, broker, pm, None, risk, ET_NAIVE)

        assert result is True
        assert not pm.has_position("SPY240714C00450000")
        broker.place_option_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancelled_no_fill_mandatory_replaces(self):
        """
        Order status CANCELLED, filled_quantity=0, is_mandatory=True.
        A replacement exit order should be placed.
        """
        from scripts.session_runner import _poll_pending_exit

        pm = _make_pm_with_exit_pending(qty=1, entry_price=2.00)
        pos = pm.get_position("SPY240714C00450000")

        cancelled_no_fill = _order_result(OrderStatus.CANCELLED, filled_qty=0)
        new_order = MagicMock()
        new_order.order_id = "ORD-EXIT-003"

        broker = _make_broker(cancelled_no_fill)
        broker.place_option_order = AsyncMock(return_value=new_order)
        broker.get_option_quote = AsyncMock(return_value=MagicMock(
            bid=Decimal("1.20"), ask=Decimal("1.40"), mid=Decimal("1.30")
        ))

        risk = _make_risk()
        await _poll_pending_exit(pos, broker, pm, None, risk, ET_NAIVE)

        broker.place_option_order.assert_called_once()
        call_args = broker.place_option_order.call_args[0][0]
        assert call_args.quantity == 1


# ═══════════════════════════════════════════════════════════════════════════════
# C. _place_exit_order guard: remaining <= 0 → nothing placed
# ═══════════════════════════════════════════════════════════════════════════════

class TestPlaceExitOrderGuard:

    @pytest.mark.asyncio
    async def test_no_order_when_remaining_zero(self):
        """
        _place_exit_order should not place an order when confirmed_fill_qty >= quantity.
        """
        from scripts.session_runner import _place_exit_order

        pm = _make_pm_with_exit_pending(qty=1, entry_price=2.00, confirmed_fill_qty=1)
        pos = pm.get_position("SPY240714C00450000")

        broker = MagicMock()
        broker.place_option_order = AsyncMock()
        broker.get_option_quote = AsyncMock(return_value=MagicMock(
            bid=Decimal("1.80"), ask=Decimal("2.00"), mid=Decimal("1.90")
        ))

        await _place_exit_order(pos, broker, pm, ET_NAIVE, "stop_loss", True)

        broker.place_option_order.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# D. Mandatory exit gate: should_exit() returns None when EXIT_PENDING=True
# ═══════════════════════════════════════════════════════════════════════════════

class TestMandatoryExitGate:

    def test_should_exit_returns_none_when_exit_pending(self):
        """
        should_exit() must return None for any EXIT_PENDING position,
        including positions whose exit condition (stop_loss, eod) is still active.
        Mandatory exits bypass CONDITION re-evaluation but not the DEDUP gate.
        """
        pm = _make_pm_with_exit_pending(qty=1, entry_price=2.00)
        pos = pm.get_position("SPY240714C00450000")
        assert pos.exit_pending is True

        # Price has fallen well below stop-loss — condition is active
        stop_loss_price = pos.entry_price * (1 - pos.stop_loss_pct)
        result = pm.should_exit("SPY240714C00450000", stop_loss_price * 0.5, ET_NAIVE)

        assert result is None, (
            "should_exit() must return None for EXIT_PENDING positions "
            "regardless of whether exit conditions are met"
        )

    def test_should_exit_resumes_after_clear(self):
        """
        After clear_exit_pending(), should_exit() resumes normal evaluation.
        """
        pm = _make_pm_with_exit_pending(qty=1, entry_price=2.00)
        pm.clear_exit_pending("SPY240714C00450000")
        pos = pm.get_position("SPY240714C00450000")
        assert pos.exit_pending is False

        # Stop-loss condition is active
        stop_loss_price = pos.entry_price * (1 - pos.stop_loss_pct) * 0.5
        result = pm.should_exit("SPY240714C00450000", stop_loss_price, ET_NAIVE)

        assert result == "stop_loss"


# ═══════════════════════════════════════════════════════════════════════════════
# E. Quantity invariant: total submitted quantity never exceeds broker position
# ═══════════════════════════════════════════════════════════════════════════════

class TestQuantityInvariant:

    @pytest.mark.asyncio
    async def test_total_submitted_qty_does_not_exceed_position(self):
        """
        Across a full reprice+cancel+fill race, the total quantity submitted
        to the broker must equal the original position size (1 contract).

        Sequence:
          1. Exit order placed (qty=1)                         → total submitted = 1
          2. Bid moves: cancel+replace requested
          3. Cancel confirms CANCELLED with filled_quantity=1  → race detected
          4. No replacement placed                             → total submitted stays 1
          5. Next poll: status=CANCELLED, confirmed_fill_qty=1 → fully_filled=True, closes
        """
        from scripts.session_runner import _poll_pending_exit

        pm = _make_pm_with_exit_pending(qty=1, entry_price=2.00)
        pos = pm.get_position("SPY240714C00450000")
        pos.exit_order_limit_price = 2.50

        placed_quantities = []

        async def _capture_order(req):
            placed_quantities.append(req.quantity)
            m = MagicMock()
            m.order_id = f"ORD-REPLACE-{len(placed_quantities)}"
            return m

        still_open = _order_result(OrderStatus.OPEN, filled_qty=0)
        cancel_with_fill = _order_result(OrderStatus.CANCELLED, filled_qty=1, filled_price=1.80)

        broker = MagicMock()
        broker.cancel_order = AsyncMock(return_value=True)
        broker.place_option_order = AsyncMock(side_effect=_capture_order)
        broker.get_order_status = AsyncMock(side_effect=[still_open, cancel_with_fill])
        broker.get_option_quote = AsyncMock(return_value=MagicMock(
            bid=Decimal("1.20"), ask=Decimal("1.40"), mid=Decimal("1.30")
        ))

        risk = _make_risk()

        # Cycle 1: reprice attempt; cancel confirms with fill → no replacement
        await _poll_pending_exit(pos, broker, pm, None, risk, ET_NAIVE)
        assert placed_quantities == [], "No replacement should be placed after fill-cancel race"

        # Cycle 2: pos still EXIT_PENDING; status now CANCELLED with filled_qty confirmed
        pos2 = pm.get_position("SPY240714C00450000")
        assert pos2 is not None
        assert pos2.confirmed_fill_qty == 1

        cancelled_confirmed = _order_result(OrderStatus.CANCELLED, filled_qty=1, filled_price=1.80)
        broker.get_order_status = AsyncMock(return_value=cancelled_confirmed)
        broker.get_option_quote = AsyncMock(return_value=MagicMock(
            bid=Decimal("1.80"), ask=Decimal("2.00"), mid=Decimal("1.90")
        ))

        result = await _poll_pending_exit(pos2, broker, pm, None, risk, ET_NAIVE)

        assert result is True, "Position should close on second poll"
        assert not pm.has_position("SPY240714C00450000")
        assert placed_quantities == [], "Total replacement orders placed: 0 (original fill covers full qty)"
