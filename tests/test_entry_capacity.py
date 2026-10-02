"""Regression tests for pending-entry capacity and ORB reservation accounting."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.brokers.broker_interface import OrderStatus
from app.risk.entry_capacity import has_entry_capacity, reserved_entry_slots
from app.risk.risk_manager import RiskManager
from app.trading.fill_tracker import FillTracker
from scripts.session_runner import _entry_capacity_used

ET = ZoneInfo("America/New_York")


def test_pending_entry_reserves_only_active_slot():
    assert reserved_entry_slots([], ["AAPL260729C00345000"]) == 1
    assert has_entry_capacity([], ["AAPL260729C00345000"], 1) is False


def test_partial_fill_same_contract_counts_once():
    symbol = "AAPL260729C00345000"
    assert reserved_entry_slots([symbol], [symbol]) == 1
    assert has_entry_capacity([symbol], [symbol], 2) is True


def test_runner_capacity_counts_untracked_risk_pending_fail_closed():
    pm = MagicMock()
    pm.open_positions.return_value = []
    fill_tracker = MagicMock()
    fill_tracker.pending_orders.return_value = []
    risk = SimpleNamespace(pending_entries=1)
    assert _entry_capacity_used(pm, fill_tracker, risk) == 1


def test_runner_capacity_deduplicates_partial_fill_symbol():
    symbol = "AAPL260729C00345000"
    pm = MagicMock()
    pm.open_positions.return_value = [SimpleNamespace(option_symbol=symbol)]
    fill_tracker = MagicMock()
    fill_tracker.pending_orders.return_value = [SimpleNamespace(option_symbol=symbol)]
    risk = SimpleNamespace(pending_entries=1)
    assert _entry_capacity_used(pm, fill_tracker, risk) == 1


def test_cancelled_non_orb_order_releases_orb_reservation():
    risk = RiskManager(SimpleNamespace())
    risk.record_entry_pending("vwap_reclaim")
    assert risk.non_orb_entry_commitments == 1
    risk.record_entry_cancelled("vwap_reclaim")
    assert risk.non_orb_entry_commitments == 0


def test_filled_non_orb_order_keeps_orb_reservation():
    risk = RiskManager(SimpleNamespace())
    risk.record_entry_pending("vwap_reclaim")
    risk.record_entry_filled("vwap_reclaim")
    assert risk.pending_entries == 0
    assert risk.non_orb_entry_commitments == 1


def test_orb_order_does_not_consume_non_orb_reservation_count():
    risk = RiskManager(SimpleNamespace())
    risk.record_entry_pending("orb")
    risk.record_entry_filled("orb")
    assert risk.non_orb_entry_commitments == 0


@pytest.mark.asyncio
async def test_fill_tracker_cancellation_releases_strategy_reservation():
    risk = RiskManager(SimpleNamespace())
    risk.record_entry_pending("vwap_reclaim")

    tracker = FillTracker()
    tracker.register(
        order_id="QQQ-CANCELLED",
        journal_id=0,
        option_symbol="QQQ260729P00661000",
        symbol="QQQ",
        strategy_id="vwap_reclaim",
        direction="SHORT",
        quantity=3,
        limit_price=0.67,
        placed_at=datetime.now(tz=ET),
    )

    status = MagicMock(status=OrderStatus.CANCELLED)
    broker = MagicMock()
    broker.get_order_status = AsyncMock(return_value=status)
    pm = MagicMock()
    pm.has_position.return_value = False

    await tracker.poll(
        broker=broker,
        pm=pm,
        journal=None,
        now=datetime.now(tz=ET),
        risk=risk,
    )

    assert tracker.count() == 0
    assert risk.pending_entries == 0
    assert risk.non_orb_entry_commitments == 0
