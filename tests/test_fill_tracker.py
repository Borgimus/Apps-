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
    """Return an async mock broker that reports the given order status."""
    order_resp = MagicMock()
    order_resp.status = status
    order_resp.filled_quantity = filled_qty
    order_resp.filled_price = filled_price

    broker = MagicMock()
    broker.get_order_status = AsyncMock(return_value=order_resp)
    broker.cancel_order = AsyncMock()
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

        asyncio.get_event_loop().run_until_complete(_run())
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
