"""
Regression tests for Bug C: exit P&L uses midpoint instead of bid/actual fill price.

2026-06-03 evidence:
  META exit: bid=$0.33 ask=$0.45 mid=$0.39; limit placed at $0.33 (bid).
             Session recorded exit_price=$0.39 (mid), pnl=-$12 instead of -$16.
  AMZN exit: bid=$0.13 ask=$0.18 mid=$0.155; limit placed at $0.13.
             Session recorded pnl=-$5.50 (mid) instead of -$8.00 (bid).

Fix: monitor_positions() and eod_liquidate() must use bid as exit_price_pnl
when the exit limit order is placed at bid.  The midpoint remains correct for
evaluating exit conditions (trailing stop triggers), but accounting must
reflect the realistic execution price.
"""

from __future__ import annotations

import pytest
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from app.brokers.broker_interface import OrderStatus

ET = ZoneInfo("America/New_York")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_pos(entry_price: float = 0.51, symbol: str = "META",
              option_symbol: str = "META260603P00605000", journal_id: int = 308):
    pos = MagicMock()
    pos.symbol = symbol
    pos.option_symbol = option_symbol
    pos.entry_price = entry_price
    pos.quantity = 1
    pos.strategy_id = "vwap_reclaim"
    pos.journal_id = journal_id
    pos.entry_time = datetime(2026, 6, 3, 12, 20, tzinfo=ET)
    # Exit-state-machine fields (real values, not MagicMock attrs, so numeric
    # comparisons in the confirmation flow behave)
    pos.exit_pending = False
    pos.exit_order_id = None
    pos.exit_order_limit_price = 0.0
    pos.confirmed_fill_qty = 0
    pos.confirmed_fill_value = 0.0
    pos.current_price = entry_price
    pos.peak_price = entry_price
    pos.trough_price = entry_price
    pos.exit_quote_bid = None
    return pos


def _make_pm(pos, exit_reason: str = "trailing_stop"):
    """Stateful PM mock for the confirmation-based exit flow: mark_exit_pending
    records exit state on the position; close_confirmed empties the open set."""
    pm = MagicMock()
    closed = []

    def _mark(option_symbol, order_id=None, limit_price=None, reason=None,
              is_mandatory=False, exit_quote_bid=None, now=None):
        pos.exit_pending = True
        pos.exit_order_id = order_id
        pos.exit_order_limit_price = limit_price
        pos.exit_triggered_reason = reason
        pos.exit_is_mandatory = is_mandatory
        pos.exit_quote_bid = exit_quote_bid

    pm.mark_exit_pending = MagicMock(side_effect=_mark)
    pm.open_positions = MagicMock(side_effect=lambda: [] if closed else [pos])
    pm.get_position = MagicMock(return_value=pos)
    pm.record_partial_fill = MagicMock()
    pm.close_confirmed = MagicMock(side_effect=lambda *a, **k: closed.append(1))
    pm.update_price = MagicMock()
    pm.should_exit = MagicMock(return_value=exit_reason)
    return pm


def _make_broker(bid: float, ask: float):
    """Broker returning a quote with given bid/ask; place_option_order succeeds."""
    quote = MagicMock()
    quote.bid = bid
    quote.ask = ask
    quote.mid = (bid + ask) / 2
    quote.timestamp = datetime.now(tz=ET)

    order_result = MagicMock()
    order_result.order_id = "exit-order-001"

    broker = MagicMock()
    broker.get_option_quote = AsyncMock(return_value=quote)
    broker.place_option_order = AsyncMock(return_value=order_result)
    return broker


def _confirm_exit_fill(broker, pos, fill_price: float):
    """Arm get_order_status so the pending exit confirms FILLED at fill_price."""
    order = MagicMock()
    order.status = OrderStatus.FILLED
    order.filled_quantity = pos.quantity
    order.filled_price = fill_price
    order.filled_at = datetime.now(tz=ET)
    broker.get_order_status = AsyncMock(return_value=order)


def _make_journal():
    j = MagicMock()
    j.record_exit = AsyncMock()
    j.mark_exit_pending = AsyncMock()
    j.log_event = AsyncMock()
    j.commit = AsyncMock()
    return j


def _make_risk():
    risk = MagicMock()
    risk.record_exit = MagicMock()
    return risk


def _make_settings(max_spread_pct: float = 0.50):
    risk = MagicMock()
    risk.max_spread_pct = max_spread_pct
    s = MagicMock()
    s.risk = risk
    return s


# ─────────────────────────────────────────────────────────────────────────────
# Bug C — monitor_positions exit pricing
# ─────────────────────────────────────────────────────────────────────────────

class TestExitPricingMonitorPositions:
    """
    monitor_positions() must price exit limit orders at bid (not mid) and the
    journal must record the broker-confirmed fill. The exit flow is two-cycle:
    cycle 1 places the limit order (EXIT_PENDING), cycle 2 confirms the fill.
    """

    async def _run_exit_flow(self, pos, pm, broker, journal,
                             risk=None, settings=None, fill_price=None):
        """Drive the two-cycle exit: place on cycle 1, confirm on cycle 2.
        By default the order fills at its own limit price (limit sell fills
        at limit). Returns the placed limit price."""
        from scripts.session_runner import monitor_positions

        risk = risk or _make_risk()
        settings = settings or _make_settings()
        now = datetime.now(tz=ET)

        await monitor_positions(
            broker=broker, pm=pm, journal=journal, risk=risk,
            now=now, dry_run=False, settings=settings,
        )
        assert pos.exit_pending, "exit order was not placed on first cycle"
        placed_limit = float(pos.exit_order_limit_price)

        _confirm_exit_fill(broker, pos, fill_price if fill_price is not None else placed_limit)
        await monitor_positions(
            broker=broker, pm=pm, journal=journal, risk=risk,
            now=now, dry_run=False, settings=settings,
        )
        return placed_limit

    @pytest.mark.asyncio
    async def test_meta_scenario_journal_uses_bid_not_mid(self):
        """
        2026-06-03 META scenario:
        entry=$0.51, exit bid=$0.33 ask=$0.45 mid=$0.39
        Old: exit_price=$0.39, pnl=-$12  (wrong)
        New: exit_price=$0.33, pnl=-$18  (correct — bid = limit price)
        """
        pos = _make_pos(entry_price=0.51)
        pm = _make_pm(pos, exit_reason="trailing_stop")
        broker = _make_broker(bid=0.33, ask=0.45)
        journal = _make_journal()

        placed_limit = await self._run_exit_flow(pos, pm, broker, journal)

        # Limit must be at bid, not mid (0.39)
        assert placed_limit == pytest.approx(0.33), (
            f"Expected exit limit=0.33 (bid), got {placed_limit}"
        )
        journal.record_exit.assert_awaited_once()
        call_kw = journal.record_exit.call_args.kwargs
        assert call_kw["exit_price"] == pytest.approx(0.33), (
            f"Expected exit_price=0.33 (confirmed fill at bid), got {call_kw['exit_price']}"
        )
        # P&L = (0.33 - 0.51) * 100 = -18.0, not -12.0 (midpoint-based)
        assert call_kw["realized_pnl"] == pytest.approx(-18.0), (
            f"Expected realized_pnl=-18.0 (bid-based), got {call_kw['realized_pnl']}"
        )

    @pytest.mark.asyncio
    async def test_amzn_scenario_journal_uses_bid_not_mid(self):
        """
        2026-06-03 AMZN scenario:
        entry=$0.21, exit bid=$0.13 ask=$0.18 mid=$0.155
        Old: exit_price=$0.155, pnl=-$5.50  (wrong)
        New: exit_price=$0.13, pnl=-$8.00   (correct)
        """
        pos = _make_pos(
            entry_price=0.21,
            symbol="AMZN",
            option_symbol="AMZN260603P00247500",
            journal_id=307,
        )
        pm = _make_pm(pos, exit_reason="trailing_stop")
        broker = _make_broker(bid=0.13, ask=0.18)
        journal = _make_journal()

        await self._run_exit_flow(pos, pm, broker, journal)

        call_kw = journal.record_exit.call_args.kwargs
        assert call_kw["exit_price"] == pytest.approx(0.13)
        assert call_kw["realized_pnl"] == pytest.approx(-8.0)

    @pytest.mark.asyncio
    async def test_exit_bid_recorded_for_spread_analysis(self):
        """The pre-order bid must be recorded on the exit for slippage/spread
        analysis (exit_bid / exit_quote_bid in the confirmed-exit record)."""
        pos = _make_pos(entry_price=0.51)
        pm = _make_pm(pos, exit_reason="trailing_stop")
        broker = _make_broker(bid=0.33, ask=0.45)
        journal = _make_journal()

        await self._run_exit_flow(pos, pm, broker, journal)

        call_kw = journal.record_exit.call_args.kwargs
        assert call_kw["exit_bid"] == pytest.approx(0.33)
        assert call_kw["exit_quote_bid"] == pytest.approx(0.33)

    @pytest.mark.asyncio
    async def test_fallback_pricing_when_bid_is_zero(self):
        """When bid=0 (no market), the exit limit falls back to 98% of the
        last known price — never the mid of a one-sided quote."""
        pos = _make_pos(entry_price=2.00)
        pm = _make_pm(pos, exit_reason="trailing_stop")
        # bid=0 simulates a stale/empty quote; last known price = 2.00
        broker = _make_broker(bid=0.0, ask=4.00)
        journal = _make_journal()

        placed_limit = await self._run_exit_flow(pos, pm, broker, journal)

        # limit = round(2.00 * 0.98, 2) = 1.96
        assert placed_limit == pytest.approx(1.96)
        call_kw = journal.record_exit.call_args.kwargs
        assert call_kw["exit_price"] == pytest.approx(1.96)

    @pytest.mark.asyncio
    async def test_pm_close_confirmed_receives_broker_fill_price(self):
        """pm.close_confirmed() must receive the broker-confirmed fill price."""
        pos = _make_pos(entry_price=0.51)
        pm = _make_pm(pos, exit_reason="trailing_stop")
        broker = _make_broker(bid=0.33, ask=0.45)

        await self._run_exit_flow(pos, pm, broker, _make_journal())

        pm.close_confirmed.assert_called_once()
        close_args = pm.close_confirmed.call_args.args
        # pm.close_confirmed(option_symbol, exit_price, pnl)
        assert close_args[1] == pytest.approx(0.33), (
            f"Expected close_confirmed exit_price=0.33 (bid fill), got {close_args[1]}"
        )

    @pytest.mark.asyncio
    async def test_stop_loss_also_uses_bid(self):
        """stop_loss exits are also bid-priced — not mid."""
        pos = _make_pos(entry_price=1.00)
        pm = _make_pm(pos, exit_reason="stop_loss")
        broker = _make_broker(bid=0.40, ask=0.70)  # mid=0.55
        journal = _make_journal()

        placed_limit = await self._run_exit_flow(pos, pm, broker, journal)

        assert placed_limit == pytest.approx(0.40)
        call_kw = journal.record_exit.call_args.kwargs
        assert call_kw["exit_price"] == pytest.approx(0.40)
        assert call_kw["realized_pnl"] == pytest.approx(-60.0)  # (0.40-1.00)*100

    @pytest.mark.asyncio
    async def test_pnl_reconciliation_matches_broker_execution(self):
        """
        Journal pnl must equal broker execution pnl. The exit limit is placed
        at bid and the broker confirms the fill; journal must reflect the
        broker execution: (0.13 - 0.21) * 100 = -$8.00.
        """
        pos = _make_pos(entry_price=0.21, symbol="AMZN",
                        option_symbol="AMZN260603P00247500")
        pm = _make_pm(pos, exit_reason="trailing_stop")
        broker = _make_broker(bid=0.13, ask=0.18)
        journal = _make_journal()

        await self._run_exit_flow(pos, pm, broker, journal)

        call_kw = journal.record_exit.call_args.kwargs
        broker_execution_pnl = (0.13 - 0.21) * 100  # -8.00
        assert call_kw["realized_pnl"] == pytest.approx(broker_execution_pnl), (
            f"Journal pnl {call_kw['realized_pnl']} does not match "
            f"broker execution pnl {broker_execution_pnl}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Bug C — eod_liquidate exit pricing
# ─────────────────────────────────────────────────────────────────────────────

class TestExitPricingEodLiquidate:
    """
    eod_liquidate() must also use bid for P&L, not midpoint.
    """

    @pytest.mark.asyncio
    async def test_eod_journal_uses_bid_not_mid(self):
        """EOD exit limit must be priced at bid (not mid); journal records the
        broker-confirmed fill. Uses wall-clock `now` because eod_liquidate's
        90s confirmation deadline compares against the real clock."""
        from scripts.session_runner import eod_liquidate

        pos = _make_pos(entry_price=2.00)
        pm = _make_pm(pos)

        broker = _make_broker(bid=1.20, ask=1.80)  # mid=1.50
        _confirm_exit_fill(broker, pos, fill_price=1.20)
        journal = _make_journal()
        risk = _make_risk()

        await eod_liquidate(
            broker=broker, pm=pm, journal=journal, risk=risk,
            now=datetime.now(tz=ET),
            dry_run=False,
        )

        # Exit limit must be bid=1.20, not mid=1.50
        placed = broker.place_option_order.call_args.args[0]
        assert float(placed.limit_price) == pytest.approx(1.20), (
            f"Expected EOD limit at bid=1.20, got {placed.limit_price}"
        )
        call_kw = journal.record_exit.call_args.kwargs
        assert call_kw["exit_price"] == pytest.approx(1.20), (
            f"Expected eod exit_price=1.20 (confirmed fill at bid), got {call_kw['exit_price']}"
        )
        assert call_kw["realized_pnl"] == pytest.approx(-80.0), (
            f"Expected eod pnl=-80.0, got {call_kw['realized_pnl']}"
        )

    @pytest.mark.asyncio
    async def test_eod_fallback_pricing_when_bid_zero(self):
        """When bid=0, the EOD limit falls back to 98% of last known price
        (never the mid of a one-sided quote)."""
        from scripts.session_runner import eod_liquidate

        pos = _make_pos(entry_price=2.00)
        pos.current_price = 1.53
        pm = _make_pm(pos)

        broker = _make_broker(bid=0.0, ask=3.00)  # mid would be 1.50
        _confirm_exit_fill(broker, pos, fill_price=1.50)
        journal = _make_journal()

        await eod_liquidate(
            broker=broker, pm=pm, journal=journal, risk=_make_risk(),
            now=datetime.now(tz=ET),
            dry_run=False,
        )

        # limit = round(current_price * 0.98, 2) = 1.50
        placed = broker.place_option_order.call_args.args[0]
        assert float(placed.limit_price) == pytest.approx(1.50)
        call_kw = journal.record_exit.call_args.kwargs
        assert call_kw["exit_price"] == pytest.approx(1.50)
