"""
Tests for FillTracker.

All broker / PM / journal interactions are mocked so the tests run offline
and deterministically.  The broker mock's get_order_status() returns a
configurable OrderResponse-like object whose status can be controlled
per-test.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.brokers.broker_interface import OrderStatus
from app.trading.fill_tracker import FillTracker, PendingOrder

ET_ZONE = "America/New_York"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_broker(status: OrderStatus, filled_qty: int = 1, filled_price: float = 3.10):
    """Return an async mock broker that reports the given order status.

    Realistic cancel semantics: after cancel_order() is awaited for an order,
    get_order_status reports CANCELED for it (unless the pre-cancel status was
    already terminal, e.g. FILLED — a cancel cannot un-fill an order). The
    FillTracker never trusts a cancel request alone; it confirms terminal
    state via get_order_status before dropping an order.
    """
    order_resp = MagicMock()
    order_resp.status = status
    order_resp.filled_quantity = filled_qty
    order_resp.filled_price = filled_price

    cancelled_ids = set()
    _TERMINAL = (
        OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.CANCELED,
        OrderStatus.REJECTED, OrderStatus.EXPIRED,
    )

    cancelled_resp = MagicMock()
    cancelled_resp.status = OrderStatus.CANCELED
    cancelled_resp.filled_quantity = 0
    cancelled_resp.filled_price = 0.0

    broker = MagicMock()

    async def _cancel(order_id):
        cancelled_ids.add(order_id)

    async def _status(order_id):
        if order_id in cancelled_ids and order_resp.status not in _TERMINAL:
            return cancelled_resp
        return order_resp

    broker.cancel_order = AsyncMock(side_effect=_cancel)
    broker.get_order_status = AsyncMock(side_effect=_status)
    return broker


def _make_pm():
    pm = MagicMock()
    pm.has_position = MagicMock(return_value=False)
    pm.open = MagicMock()
    pm._positions = {}
    return pm


def _make_journal():
    journal = MagicMock()
    journal.record_fill = AsyncMock()
    journal.record_cancellation = AsyncMock()
    journal.log_event = AsyncMock()
    journal.commit = AsyncMock()
    return journal


def _register(ft: FillTracker, order_id: str = "ORD-001", placed_at: datetime | None = None):
    if placed_at is None:
        placed_at = datetime.now()
    return ft.register(
        order_id=order_id,
        journal_id=42,
        option_symbol="SPY240102C00450000",
        symbol="SPY",
        strategy_id="orb",
        direction="LONG",
        quantity=1,
        limit_price=3.00,
        placed_at=placed_at,
    )


# ── Registration ──────────────────────────────────────────────────────────────

class TestRegistration:

    def test_register_adds_to_pending(self):
        ft = FillTracker()
        _register(ft)
        assert ft.count() == 1

    def test_has_pending_for_symbol(self):
        ft = FillTracker()
        _register(ft)
        assert ft.has_pending_for_symbol("SPY") is True
        assert ft.has_pending_for_symbol("QQQ") is False

    def test_pending_orders_list(self):
        ft = FillTracker()
        _register(ft, "A")
        _register(ft, "B")
        ids = [p.order_id for p in ft.pending_orders()]
        assert "A" in ids and "B" in ids

    def test_count_reflects_pending(self):
        ft = FillTracker()
        assert ft.count() == 0
        _register(ft, "X")
        assert ft.count() == 1


# ── Full fill ─────────────────────────────────────────────────────────────────

class TestFullFill:

    @pytest.mark.asyncio
    async def test_full_fill_opens_position(self):
        ft = FillTracker()
        _register(ft, "ORD-FULL")

        broker = _make_broker(OrderStatus.FILLED, filled_qty=1, filled_price=3.10)
        pm = _make_pm()
        journal = _make_journal()
        now = datetime.now()

        fills = await ft.poll(broker, pm, journal, now)

        assert fills == 1
        pm.open.assert_called_once()
        call_kw = pm.open.call_args.kwargs
        assert call_kw["entry_price"] == pytest.approx(3.10)
        assert call_kw["option_symbol"] == "SPY240102C00450000"

    @pytest.mark.asyncio
    async def test_full_fill_removes_from_pending(self):
        ft = FillTracker()
        _register(ft, "ORD-GONE")

        broker = _make_broker(OrderStatus.FILLED)
        pm = _make_pm()
        journal = _make_journal()

        await ft.poll(broker, pm, journal, datetime.now())

        assert ft.count() == 0
        assert ft.has_pending_for_symbol("SPY") is False

    @pytest.mark.asyncio
    async def test_full_fill_records_journal(self):
        ft = FillTracker()
        _register(ft, "ORD-J")

        broker = _make_broker(OrderStatus.FILLED, filled_price=3.05)
        pm = _make_pm()
        journal = _make_journal()

        await ft.poll(broker, pm, journal, datetime.now())

        journal.record_fill.assert_awaited_once()
        journal.commit.assert_awaited()


# ── Partial fill ──────────────────────────────────────────────────────────────

class TestPartialFill:

    @pytest.mark.asyncio
    async def test_partial_fill_opens_position_for_filled_qty(self):
        ft = FillTracker()
        _register(ft, "ORD-PART")

        broker = _make_broker(OrderStatus.PARTIALLY_FILLED, filled_qty=1, filled_price=3.08)
        pm = _make_pm()
        journal = _make_journal()

        fills = await ft.poll(broker, pm, journal, datetime.now())

        assert fills == 1
        pm.open.assert_called_once()
        kw = pm.open.call_args.kwargs
        assert kw["quantity"] == 1
        assert kw["entry_price"] == pytest.approx(3.08)

    @pytest.mark.asyncio
    async def test_partial_fill_stays_in_pending(self):
        ft = FillTracker()
        _register(ft, "ORD-STAY")

        broker = _make_broker(OrderStatus.PARTIALLY_FILLED, filled_qty=1)
        pm = _make_pm()
        journal = _make_journal()

        await ft.poll(broker, pm, journal, datetime.now())

        # Order should still be tracked (waiting for remainder)
        assert ft.count() == 1

    @pytest.mark.asyncio
    async def test_partial_then_full_fill(self):
        ft = FillTracker()
        _register(ft, "ORD-SEQ")

        partial_broker = _make_broker(OrderStatus.PARTIALLY_FILLED, filled_qty=1)
        pm = _make_pm()
        journal = _make_journal()
        now = datetime.now()

        await ft.poll(partial_broker, pm, journal, now)
        assert ft.count() == 1  # still pending

        # Now position is already open; second poll marks full fill
        pm.has_position = MagicMock(return_value=True)
        full_broker = _make_broker(OrderStatus.FILLED, filled_qty=2)
        await ft.poll(full_broker, pm, journal, now)
        assert ft.count() == 0  # removed on full fill


# ── Cancellation / rejection / expiry ────────────────────────────────────────

class TestDeadOrders:

    @pytest.mark.asyncio
    async def test_cancelled_removes_from_pending(self):
        ft = FillTracker()
        _register(ft, "ORD-CAN")

        broker = _make_broker(OrderStatus.CANCELLED)
        pm = _make_pm()
        journal = _make_journal()

        await ft.poll(broker, pm, journal, datetime.now())

        assert ft.count() == 0

    @pytest.mark.asyncio
    async def test_rejected_removes_phantom_position(self):
        ft = FillTracker()
        _register(ft, "ORD-REJ")

        broker = _make_broker(OrderStatus.REJECTED)
        pm = _make_pm()
        # Simulate a phantom position being open in PM
        phantom_sym = "SPY240102C00450000"
        pm.has_position = MagicMock(return_value=True)
        pm._positions[phantom_sym] = MagicMock()

        journal = _make_journal()

        await ft.poll(broker, pm, journal, datetime.now())

        assert phantom_sym not in pm._positions
        journal.record_cancellation.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_expired_order_recorded(self):
        ft = FillTracker()
        _register(ft, "ORD-EXP")

        broker = _make_broker(OrderStatus.EXPIRED)
        pm = _make_pm()
        journal = _make_journal()

        await ft.poll(broker, pm, journal, datetime.now())

        assert ft.count() == 0
        journal.record_cancellation.assert_awaited_once()
        kw = journal.record_cancellation.call_args.kwargs
        assert kw["reason"] == "expired"


# ── Stale order auto-cancellation ─────────────────────────────────────────────

class TestStaleOrder:

    @pytest.mark.asyncio
    async def test_stale_order_is_cancelled_and_removed(self):
        ft = FillTracker(max_age_minutes=10)

        # Place an order 15 minutes ago
        old_placed_at = datetime.now() - timedelta(minutes=15)
        _register(ft, "ORD-STALE", placed_at=old_placed_at)

        broker = _make_broker(OrderStatus.NEW)  # still "open" on broker side
        pm = _make_pm()
        journal = _make_journal()

        await ft.poll(broker, pm, journal, datetime.now())

        broker.cancel_order.assert_awaited_once_with("ORD-STALE")
        assert ft.count() == 0
        journal.record_cancellation.assert_awaited_once()
        kw = journal.record_cancellation.call_args.kwargs
        assert "stale" in kw["reason"]

    @pytest.mark.asyncio
    async def test_fresh_order_is_not_auto_cancelled(self):
        ft = FillTracker(max_age_minutes=30)

        _register(ft, "ORD-FRESH")

        broker = _make_broker(OrderStatus.NEW)
        pm = _make_pm()
        journal = _make_journal()

        await ft.poll(broker, pm, journal, datetime.now())

        broker.cancel_order.assert_not_awaited()
        assert ft.count() == 1


# ── Dedup via has_pending_for_symbol ─────────────────────────────────────────

class TestDedup:

    def test_has_pending_blocks_duplicate(self):
        ft = FillTracker()
        _register(ft, "ORD-A")

        assert ft.has_pending_for_symbol("SPY") is True

    def test_after_fill_dedup_clears(self):
        ft = FillTracker()
        _register(ft, "ORD-B")

        async def _run():
            broker = _make_broker(OrderStatus.FILLED)
            pm = _make_pm()
            journal = _make_journal()
            await ft.poll(broker, pm, journal, datetime.now())

        asyncio.run(_run())
        assert ft.has_pending_for_symbol("SPY") is False

    def test_multiple_symbols_isolated(self):
        ft = FillTracker()

        ft.register(
            order_id="ORD-SPY",
            journal_id=1,
            option_symbol="SPY240102C00450000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            quantity=1,
            limit_price=3.00,
        )

        assert ft.has_pending_for_symbol("SPY") is True
        assert ft.has_pending_for_symbol("QQQ") is False


# ── Broker failure resilience ─────────────────────────────────────────────────

class TestBrokerFailure:

    @pytest.mark.asyncio
    async def test_broker_error_keeps_order_pending(self):
        ft = FillTracker()
        _register(ft, "ORD-ERR")

        broker = MagicMock()
        broker.get_order_status = AsyncMock(side_effect=RuntimeError("network timeout"))
        pm = _make_pm()
        journal = _make_journal()

        fills = await ft.poll(broker, pm, journal, datetime.now())

        assert fills == 0
        assert ft.count() == 1  # still tracked; will retry next cycle


# ── Bug A + B: stale-cancel risk counter and 422 fill recovery ────────────────
#
# Regression suite for the 2026-06-03 AMZN / COIN / NVDA scenarios:
#   Bug A: stale cancel path was missing the `risk` argument to _handle_dead(),
#          so record_entry_cancelled() was never called → _pending_entries leaked,
#          eventually falsely triggering max_entries (3/3) for COIN and NVDA.
#   Bug B: when broker returns 422 on cancel (order already filled), FillTracker
#          was calling _handle_dead("stale_cancelled") without re-checking status,
#          silently dropping the fill → orphan position until Reconciler ran.


def _make_risk():
    """Risk mock that tracks record_entry_cancelled / record_entry_filled calls."""
    risk = MagicMock()
    risk.record_entry_cancelled = MagicMock()
    risk.record_entry_filled = MagicMock()
    return risk


def _make_stale_broker():
    """Broker where cancel succeeds and the order then reports CANCELED
    (normal stale path with realistic async-cancel semantics)."""
    broker = MagicMock()
    cancelled_ids = set()

    async def _cancel(order_id):
        cancelled_ids.add(order_id)

    async def _status(order_id):
        status = OrderStatus.CANCELED if order_id in cancelled_ids else OrderStatus.NEW
        return MagicMock(status=status, filled_quantity=0, filled_price=None)

    broker.cancel_order = AsyncMock(side_effect=_cancel)
    broker.get_order_status = AsyncMock(side_effect=_status)
    return broker


class TestStaleOrderRiskIntegration:
    """
    Bug A: stale-cancel path must pass risk so pending_entries decrements.
    Bug B: 422 on cancel must re-fetch and route to _handle_fill if filled.
    """

    # ── Bug A ─────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_stale_cancel_calls_record_entry_cancelled(self):
        """Stale cancel must decrement pending_entries via record_entry_cancelled."""
        ft = FillTracker(max_age_minutes=1)
        _register(ft, "STALE-A", placed_at=datetime.now() - timedelta(minutes=5))

        broker = _make_stale_broker()
        pm = _make_pm()
        journal = _make_journal()
        risk = _make_risk()

        await ft.poll(broker, pm, journal, datetime.now(), risk=risk)

        risk.record_entry_cancelled.assert_called_once()

    @pytest.mark.asyncio
    async def test_two_stale_cancels_both_decrement_pending(self):
        """
        Reproduces the COIN/NVDA blockage: SMH + AMZN stale-cancelled but
        pending_entries never decremented → false 3/3 cap.
        Both cancels must call record_entry_cancelled so the slots are freed.
        """
        ft = FillTracker(max_age_minutes=1)
        old = datetime.now() - timedelta(minutes=5)
        _register(ft, "SMH-306", placed_at=old)

        # Register second order with different symbol to avoid duplicate detection
        ft.register(
            order_id="AMZN-307",
            journal_id=43,
            option_symbol="AMZN260603P00247500",
            symbol="AMZN",
            strategy_id="orb",
            direction="SHORT",
            quantity=1,
            limit_price=0.21,
            placed_at=old,
        )

        broker = _make_stale_broker()
        pm = _make_pm()
        journal = _make_journal()
        risk = _make_risk()

        await ft.poll(broker, pm, journal, datetime.now(), risk=risk)

        assert risk.record_entry_cancelled.call_count == 2
        assert ft.count() == 0

    @pytest.mark.asyncio
    async def test_stale_cancel_removes_from_pending(self):
        """After stale cancel, order must be removed from pending regardless of risk."""
        ft = FillTracker(max_age_minutes=1)
        _register(ft, "STALE-REM", placed_at=datetime.now() - timedelta(minutes=5))

        await ft.poll(_make_stale_broker(), _make_pm(), _make_journal(), datetime.now(), risk=_make_risk())

        assert ft.count() == 0

    # ── Bug C: cancel succeeds but order filled at exchange ──────────────────

    @pytest.mark.asyncio
    async def test_successful_cancel_but_filled_routes_to_fill_handler(self):
        """
        Reproduces the S2-S4 stale-cancel pattern (8/8 occurrences):
        - Stale cancel request returns 2xx (request accepted, async)
        - The order had already filled (or fills in flight) at the exchange
        - Post-cancel status recheck reports FILLED
        - FillTracker must route to _handle_fill, NOT _handle_dead — previously
          it trusted the cancel success and dropped the position, leaving it
          unmanaged until the next reconciler pass (10-15 min blind window).
        """
        ft = FillTracker(max_age_minutes=1)
        _register(ft, "IWM-S4", placed_at=datetime.now() - timedelta(minutes=5))

        fill_resp = MagicMock()
        fill_resp.status = OrderStatus.FILLED
        fill_resp.filled_quantity = 1
        fill_resp.filled_price = 0.28

        broker = MagicMock()
        broker.cancel_order = AsyncMock()  # succeeds — but order filled anyway
        broker.get_order_status = AsyncMock(return_value=fill_resp)

        pm = _make_pm()
        journal = _make_journal()
        risk = _make_risk()

        fills = await ft.poll(broker, pm, journal, datetime.now(), risk=risk)

        assert fills == 1
        pm.open.assert_called_once()
        journal.record_cancellation.assert_not_awaited()
        risk.record_entry_cancelled.assert_not_called()
        assert ft.count() == 0

    @pytest.mark.asyncio
    async def test_cancel_in_flight_defers_to_next_poll(self):
        """
        If the post-cancel recheck shows a still-live status (cancel not yet
        processed at the exchange), the order must STAY tracked — never
        assume dead while the broker may still report a fill.
        """
        ft = FillTracker(max_age_minutes=1)
        _register(ft, "INFLIGHT-1", placed_at=datetime.now() - timedelta(minutes=5))

        live_resp = MagicMock()
        live_resp.status = OrderStatus.NEW
        live_resp.filled_quantity = 0
        live_resp.filled_price = None

        broker = MagicMock()
        broker.cancel_order = AsyncMock()
        broker.get_order_status = AsyncMock(return_value=live_resp)

        pm = _make_pm()
        journal = _make_journal()
        risk = _make_risk()

        fills = await ft.poll(broker, pm, journal, datetime.now(), risk=risk)

        assert fills == 0
        assert ft.count() == 1          # still tracked
        journal.record_cancellation.assert_not_awaited()
        risk.record_entry_cancelled.assert_not_called()

    # ── Bug B ─────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_cancel_422_routes_to_fill_handler(self):
        """
        Reproduces the 2026-06-03 AMZN scenario:
        - Order placed at 11:37, filled at 11:39:02
        - Stale cancel fires at 11:39:09 (7 seconds after fill)
        - Broker returns 422 (order already filled)
        - FillTracker must detect fill via re-check and call _handle_fill,
          NOT _handle_dead.
        """
        ft = FillTracker(max_age_minutes=1)
        _register(ft, "AMZN-422", placed_at=datetime.now() - timedelta(minutes=5))

        fill_resp = MagicMock()
        fill_resp.status = OrderStatus.FILLED
        fill_resp.filled_quantity = 1
        fill_resp.filled_price = 0.21

        broker = MagicMock()
        broker.cancel_order = AsyncMock(side_effect=RuntimeError("HTTP 422: order already filled"))
        broker.get_order_status = AsyncMock(return_value=fill_resp)

        pm = _make_pm()
        journal = _make_journal()
        risk = _make_risk()

        fills = await ft.poll(broker, pm, journal, datetime.now(), risk=risk)

        # Must detect fill
        assert fills == 1
        pm.open.assert_called_once()
        kw = pm.open.call_args.kwargs
        assert kw["entry_price"] == pytest.approx(0.21)
        assert kw["option_symbol"] == "SPY240102C00450000"

        # Must record fill in journal, NOT cancellation
        journal.record_fill.assert_awaited_once()
        journal.record_cancellation.assert_not_awaited()

        # Must call record_entry_filled, NOT record_entry_cancelled
        risk.record_entry_filled.assert_called_once()
        risk.record_entry_cancelled.assert_not_called()

        # Order removed from pending
        assert ft.count() == 0

    @pytest.mark.asyncio
    async def test_cancel_422_partial_fill_routes_to_partial_handler(self):
        """Partial fill after 422 must call _handle_fill with partial=True."""
        ft = FillTracker(max_age_minutes=1)
        _register(ft, "PART-422", placed_at=datetime.now() - timedelta(minutes=5))

        partial_resp = MagicMock()
        partial_resp.status = OrderStatus.PARTIALLY_FILLED
        partial_resp.filled_quantity = 1
        partial_resp.filled_price = 0.21

        broker = MagicMock()
        broker.cancel_order = AsyncMock(side_effect=RuntimeError("HTTP 422"))
        broker.get_order_status = AsyncMock(return_value=partial_resp)

        pm = _make_pm()
        journal = _make_journal()

        fills = await ft.poll(broker, pm, journal, datetime.now())

        assert fills == 1
        pm.open.assert_called_once()
        # Stays in pending for remainder
        assert ft.count() == 1

    @pytest.mark.asyncio
    async def test_cancel_422_recheck_failure_falls_back_to_dead(self):
        """If both cancel AND re-check status fail, treat as stale_cancelled (safe fallback)."""
        ft = FillTracker(max_age_minutes=1)
        _register(ft, "DBLF-422", placed_at=datetime.now() - timedelta(minutes=5))

        broker = MagicMock()
        broker.cancel_order = AsyncMock(side_effect=RuntimeError("HTTP 422"))
        broker.get_order_status = AsyncMock(side_effect=RuntimeError("network timeout"))

        pm = _make_pm()
        journal = _make_journal()
        risk = _make_risk()

        fills = await ft.poll(broker, pm, journal, datetime.now(), risk=risk)

        # Falls back to dead
        assert fills == 0
        journal.record_cancellation.assert_awaited_once()
        kw = journal.record_cancellation.call_args.kwargs
        assert "stale" in kw["reason"]
        # Slot must still be freed
        risk.record_entry_cancelled.assert_called_once()
        assert ft.count() == 0

    @pytest.mark.asyncio
    async def test_cancel_422_confirmed_dead_routes_to_dead(self):
        """If re-check confirms CANCELLED state (not filled), use _handle_dead normally."""
        ft = FillTracker(max_age_minutes=1)
        _register(ft, "CONF-DEAD", placed_at=datetime.now() - timedelta(minutes=5))

        dead_resp = MagicMock()
        dead_resp.status = OrderStatus.CANCELLED
        dead_resp.filled_quantity = 0
        dead_resp.filled_price = None

        broker = MagicMock()
        broker.cancel_order = AsyncMock(side_effect=RuntimeError("HTTP 422"))
        broker.get_order_status = AsyncMock(return_value=dead_resp)

        pm = _make_pm()
        journal = _make_journal()
        risk = _make_risk()

        fills = await ft.poll(broker, pm, journal, datetime.now(), risk=risk)

        assert fills == 0
        journal.record_cancellation.assert_awaited_once()
        pm.open.assert_not_called()
        risk.record_entry_cancelled.assert_called_once()
        assert ft.count() == 0

    @pytest.mark.asyncio
    async def test_amzn_scenario_no_orphan_position(self):
        """
        Full 2026-06-03 AMZN scenario end-to-end:
        1. AMZN order placed (pending=1)
        2. Order fills at broker before stale timeout
        3. Stale cancel fires 7 seconds later → 422
        4. Re-check confirms FILLED
        5. _handle_fill called → position opened, journal filled, pending=0
        6. No orphan: position_manager.open() called exactly once
        7. No journal cancellation recorded
        """
        ft = FillTracker(max_age_minutes=1)
        ft.register(
            order_id="AMZN-260603",
            journal_id=307,
            option_symbol="AMZN260603P00247500",
            symbol="AMZN",
            strategy_id="orb",
            direction="SHORT",
            quantity=1,
            limit_price=0.21,
            placed_at=datetime.now() - timedelta(minutes=2),
        )

        fill_resp = MagicMock()
        fill_resp.status = OrderStatus.FILLED
        fill_resp.filled_quantity = 1
        fill_resp.filled_price = 0.21

        broker = MagicMock()
        broker.cancel_order = AsyncMock(side_effect=RuntimeError("HTTP 422: unprocessable entity"))
        broker.get_order_status = AsyncMock(return_value=fill_resp)

        pm = _make_pm()
        journal = _make_journal()
        risk = _make_risk()

        fills = await ft.poll(broker, pm, journal, datetime.now(), risk=risk)

        # Fill recorded
        assert fills == 1
        assert ft.count() == 0
        assert ft.has_pending_for_symbol("AMZN") is False

        # Position opened once with correct entry price
        pm.open.assert_called_once()
        assert pm.open.call_args.kwargs["entry_price"] == pytest.approx(0.21)
        assert pm.open.call_args.kwargs["strategy_id"] == "orb"

        # Journal fill recorded, cancellation NOT recorded
        journal.record_fill.assert_awaited_once()
        journal.record_cancellation.assert_not_awaited()

        # Risk: filled not cancelled
        risk.record_entry_filled.assert_called_once()
        risk.record_entry_cancelled.assert_not_called()
