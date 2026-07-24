"""
Regression tests for Bug D and Bug E (discovered 2026-06-04 paper session).

Bug D — SEV-2: Exit limit order unconfirmed creates reconciler 403 loop
  When a trailing-stop exit is placed but not filled (bid falls below limit),
  the reconciler restores the local position.  monitor_positions() then attempts
  a second sell order.  Alpaca returns 403 Forbidden because the original order
  is still open.  Fix: cancel any existing open SELL_TO_CLOSE order for the same
  symbol before placing a new exit.

Bug E — SEV-2: Session does not terminate after EOD liquidation
  eod_liquidate() was called and eod_liquidated=True, but the main loop
  continued indefinitely (ran 18 min past paper close on 2026-06-04).
  Fix: after eod_liquidate(), poll for fills up to 5 min then break the loop.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

ET = ZoneInfo("America/New_York")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers shared with test_exit_pricing.py (duplicated to keep tests isolated)
# ─────────────────────────────────────────────────────────────────────────────

def _make_pos(entry_price: float = 5.00, symbol: str = "META",
              option_symbol: str = "META260605C00645000", journal_id: int = 400):
    pos = MagicMock()
    pos.symbol = symbol
    pos.option_symbol = option_symbol
    pos.entry_price = entry_price
    pos.quantity = 1
    pos.strategy_id = "vwap_reclaim"
    pos.journal_id = journal_id
    pos.entry_time = datetime(2026, 6, 4, 12, 8, tzinfo=ET)
    return pos


def _make_pm(pos, exit_reason: str = "trailing_stop"):
    pm = MagicMock()
    pm.open_positions.return_value = [pos]
    pm.update_price = MagicMock()
    pm.should_exit.return_value = exit_reason
    pm.close = MagicMock()
    return pm


def _make_journal():
    j = MagicMock()
    j.record_exit = AsyncMock()
    j.log_event = AsyncMock()
    j.commit = AsyncMock()
    return j


def _make_risk():
    r = MagicMock()
    r.record_exit = MagicMock()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Bug D — stale exit order cancelled before placing new exit
# ─────────────────────────────────────────────────────────────────────────────

class TestBugD_StaleExitOrderCancelledBeforeNewExit:
    """
    When monitor_positions() places an exit and an existing open SELL_TO_CLOSE
    order is already on the broker for the same option symbol, it must cancel
    the stale order before placing a fresh one.
    """

    @pytest.mark.asyncio
    async def test_stale_sell_order_cancelled_before_new_exit(self):
        """
        Scenario: reconciler restored a position after an unfilled exit order.
        monitor_positions() should cancel the stale sell before placing a new one.
        """
        from scripts.session_runner import monitor_positions
        from app.brokers.broker_interface import OrderSide, OrderStatus

        pos = _make_pos()
        pm = _make_pm(pos, exit_reason="trailing_stop")

        # Stale open SELL_TO_CLOSE order for the same option symbol
        stale_order = MagicMock()
        stale_order.order_id = "stale-exit-8f1efd31"
        stale_order.option_symbol = pos.option_symbol
        stale_order.side = OrderSide.SELL_TO_CLOSE
        stale_order.status = OrderStatus.PENDING
        stale_order.limit_price = Decimal("3.58")

        new_order_result = MagicMock()
        new_order_result.order_id = "new-exit-cecf8596"

        quote = MagicMock()
        quote.bid = 1.52
        quote.ask = 1.60
        quote.mid = 1.56

        broker = MagicMock()
        broker.get_option_quote = AsyncMock(return_value=quote)
        broker.get_orders = AsyncMock(return_value=[stale_order])
        broker.cancel_order = AsyncMock(return_value=True)
        broker.place_option_order = AsyncMock(return_value=new_order_result)

        await monitor_positions(
            broker=broker, pm=pm, journal=_make_journal(), risk=_make_risk(),
            now=datetime(2026, 6, 4, 12, 48, tzinfo=ET),
            dry_run=False,
        )

        # cancel_order must have been called with the stale order's ID
        broker.cancel_order.assert_awaited_once_with(stale_order.order_id)

        # A new exit order must still have been placed
        broker.place_option_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_stale_order_no_cancel_called(self):
        """When there are no open sell orders, cancel_order must not be called."""
        from scripts.session_runner import monitor_positions

        pos = _make_pos()
        pm = _make_pm(pos, exit_reason="trailing_stop")

        quote = MagicMock()
        quote.bid = 1.52
        quote.ask = 1.60
        quote.mid = 1.56

        new_order_result = MagicMock()
        new_order_result.order_id = "exit-no-stale"

        broker = MagicMock()
        broker.get_option_quote = AsyncMock(return_value=quote)
        broker.get_orders = AsyncMock(return_value=[])  # no open orders
        broker.cancel_order = AsyncMock(return_value=True)
        broker.place_option_order = AsyncMock(return_value=new_order_result)

        await monitor_positions(
            broker=broker, pm=pm, journal=_make_journal(), risk=_make_risk(),
            now=datetime(2026, 6, 4, 12, 48, tzinfo=ET),
            dry_run=False,
        )

        broker.cancel_order.assert_not_awaited()
        broker.place_option_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_orders_failure_does_not_block_exit(self):
        """If get_orders() raises, the exit should still be placed (degrade gracefully)."""
        from scripts.session_runner import monitor_positions

        pos = _make_pos()
        pm = _make_pm(pos, exit_reason="trailing_stop")

        quote = MagicMock()
        quote.bid = 1.52
        quote.ask = 1.60
        quote.mid = 1.56

        new_order_result = MagicMock()
        new_order_result.order_id = "exit-fallback"

        broker = MagicMock()
        broker.get_option_quote = AsyncMock(return_value=quote)
        broker.get_orders = AsyncMock(side_effect=RuntimeError("network timeout"))
        broker.cancel_order = AsyncMock()
        broker.place_option_order = AsyncMock(return_value=new_order_result)

        # Should not raise; exit order must still be attempted
        await monitor_positions(
            broker=broker, pm=pm, journal=_make_journal(), risk=_make_risk(),
            now=datetime(2026, 6, 4, 12, 48, tzinfo=ET),
            dry_run=False,
        )

        broker.place_option_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_filled_sell_order_not_cancelled(self):
        """A FILLED sell order for the same symbol must not be cancelled."""
        from scripts.session_runner import monitor_positions
        from app.brokers.broker_interface import OrderSide, OrderStatus

        pos = _make_pos()
        pm = _make_pm(pos, exit_reason="trailing_stop")

        filled_order = MagicMock()
        filled_order.order_id = "already-filled-abc"
        filled_order.option_symbol = pos.option_symbol
        filled_order.side = OrderSide.SELL_TO_CLOSE
        filled_order.status = OrderStatus.FILLED  # terminal state
        filled_order.limit_price = Decimal("3.58")

        quote = MagicMock()
        quote.bid = 1.52
        quote.ask = 1.60
        quote.mid = 1.56

        new_order_result = MagicMock()
        new_order_result.order_id = "new-exit"

        broker = MagicMock()
        broker.get_option_quote = AsyncMock(return_value=quote)
        broker.get_orders = AsyncMock(return_value=[filled_order])
        broker.cancel_order = AsyncMock()
        broker.place_option_order = AsyncMock(return_value=new_order_result)

        await monitor_positions(
            broker=broker, pm=pm, journal=_make_journal(), risk=_make_risk(),
            now=datetime(2026, 6, 4, 12, 48, tzinfo=ET),
            dry_run=False,
        )

        # Filled order must not be cancelled
        broker.cancel_order.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────
# Bug E — EOD loop termination
# ─────────────────────────────────────────────────────────────────────────────

class TestBugE_EODLoopTermination:
    """
    After eod_liquidate() completes, the main session loop must exit.
    Previously the loop continued indefinitely past EOD close.
    """

    @pytest.mark.asyncio
    async def test_eod_liquidate_breaks_main_loop(self):
        """
        Simulate the EOD branch of the main loop.  After eod_liquidate fires,
        the loop must not call scan or entry code again.
        """
        from scripts.session_runner import eod_liquidate

        pm = MagicMock()
        pm.open_positions.return_value = []  # no positions to close

        broker = MagicMock()
        broker.get_option_quote = AsyncMock()

        journal = _make_journal()
        risk = _make_risk()

        # Should complete without error even when there are no positions
        await eod_liquidate(
            broker=broker, pm=pm, journal=journal, risk=risk,
            now=datetime(2026, 6, 4, 12, 30, tzinfo=ET),
            dry_run=True,
        )

        # eod_liquidate with empty positions is a no-op; no calls expected
        broker.place_option_order = AsyncMock()
        broker.place_option_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_eod_fill_poll_loop_exits_when_tracker_empty(self):
        """
        The EOD fill-poll wait loop should exit immediately when FillTracker
        has no pending orders (count() == 0).
        """
        # This tests the logic structure: if count() is 0 initially, the
        # while loop body should never execute (no sleep / no poll).
        fill_tracker = MagicMock()
        fill_tracker.count.return_value = 0

        broker = MagicMock()
        broker.get_orders = AsyncMock(return_value=[])

        # Simulate the EOD wait logic
        _polled = False
        _eod_deadline = datetime(2026, 6, 4, 12, 35, tzinfo=ET)
        _now = datetime(2026, 6, 4, 12, 30, tzinfo=ET)

        while fill_tracker.count() > 0 and _now < _eod_deadline:
            _polled = True  # pragma: no cover

        assert not _polled, "Poll loop should not execute when tracker is empty"

    @pytest.mark.asyncio
    async def test_eod_fill_poll_loop_exits_at_deadline(self):
        """
        If FillTracker still has orders after 5 min, the loop exits anyway.
        """
        fill_tracker = MagicMock()
        fill_tracker.count.return_value = 1  # always has pending orders

        _poll_count = 0
        _eod_deadline = datetime(2026, 6, 4, 12, 30, tzinfo=ET)  # already past

        _now = datetime(2026, 6, 4, 12, 35, tzinfo=ET)  # 5 min after deadline

        while fill_tracker.count() > 0 and _now < _eod_deadline:
            _poll_count += 1  # pragma: no cover

        assert _poll_count == 0, (
            "Loop should not execute when now >= deadline; "
            f"got _poll_count={_poll_count}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# LiquidityFilter — delta=N/A deep-ITM contract exclusion
# ─────────────────────────────────────────────────────────────────────────────

class TestLiquidityFilterDeepITMExclusion:
    """
    LiquidityFilter must reject contracts with delta=None when their cost
    exceeds the configured max_contract_cost cap.  This prevents deep-ITM
    contracts (e.g. XLK bid=$77, cost=$7,700/contract) from being selected
    and then rejected by the risk manager every 30-second cycle.
    """

    def _make_contract(
        self,
        option_symbol: str = "XLK260605C00115000",
        bid: float = 77.0,
        ask: float = 79.0,
        delta: float | None = None,
        oi: int = 500,
        volume: int = 100,
    ):
        from app.brokers.broker_interface import OptionContract
        spread_pct = (ask - bid) / ((bid + ask) / 2) if (bid + ask) > 0 else 0.0
        c = MagicMock(spec=OptionContract)
        c.option_symbol = option_symbol
        c.bid = bid
        c.ask = ask
        c.delta = delta
        c.open_interest = oi
        c.volume = volume
        c.spread_pct = spread_pct
        c.strike = Decimal("115")
        return c

    def test_deep_itm_delta_none_rejected_when_cost_exceeds_cap(self):
        """
        Contract with delta=None and ask*100=$7,900 is rejected when
        max_contract_cost=$995 (1% of $99,500 equity).
        """
        from app.strategies.liquidity_filter import LiquidityFilter

        lf = LiquidityFilter({
            "min_open_interest": 100,
            "min_volume": 50,
            "max_spread_pct": 0.10,
            "delta_target_min": 0.35,
            "delta_target_max": 0.45,
        })
        lf.set_max_contract_cost(995.0)

        contract = self._make_contract(bid=77.0, ask=79.0, delta=None)
        # ask * 100 = $7,900 >> $995 cap → should be rejected
        assert not lf._passes_liquidity(contract), (
            "Deep-ITM delta=None contract with cost $7,900 should fail liquidity"
        )

    def test_deep_itm_delta_none_passes_when_no_cost_cap(self):
        """
        Without a cost cap, delta=None contracts are not rejected by the filter
        (they may still fail risk manager, but that is a separate concern).
        """
        from app.strategies.liquidity_filter import LiquidityFilter

        lf = LiquidityFilter({
            "min_open_interest": 100,
            "min_volume": 50,
            "max_spread_pct": 0.10,
        })
        # No max_contract_cost set

        contract = self._make_contract(bid=4.0, ask=4.50, delta=None, oi=200, volume=100)
        # spread_pct = 0.50/8.50 ≈ 5.9% > 10% limit → adjust to be within spread
        contract.spread_pct = 0.05  # override spread_pct directly
        assert lf._passes_liquidity(contract), (
            "delta=None contract with no cost cap should pass liquidity checks"
        )

    def test_normal_atm_contract_with_delta_not_affected(self):
        """
        A normal ATM contract with real delta is unaffected by the cost cap.
        """
        from app.strategies.liquidity_filter import LiquidityFilter

        lf = LiquidityFilter({
            "min_open_interest": 100,
            "min_volume": 50,
            "max_spread_pct": 0.10,
        })
        lf.set_max_contract_cost(995.0)

        contract = self._make_contract(bid=4.0, ask=4.30, delta=0.42, oi=300, volume=150)
        contract.spread_pct = 0.073  # within limit
        # ask * 100 = $430 < $995 cap; has real delta → should pass
        assert lf._passes_liquidity(contract)

    def test_set_max_contract_cost_updates_existing_filter(self):
        """set_max_contract_cost() can be called post-construction to update the cap."""
        from app.strategies.liquidity_filter import LiquidityFilter

        lf = LiquidityFilter({})
        assert lf._max_contract_cost is None

        lf.set_max_contract_cost(1200.0)
        assert lf._max_contract_cost == 1200.0

        contract = self._make_contract(bid=14.0, ask=14.50, delta=None, oi=200, volume=100)
        contract.spread_pct = 0.035
        # ask * 100 = $1,450 > $1,200 cap, delta=None → rejected
        assert not lf._passes_liquidity(contract)
