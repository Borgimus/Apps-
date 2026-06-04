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
    return pos


def _make_pm(pos, exit_reason: str = "trailing_stop"):
    pm = MagicMock()
    pm.open_positions.return_value = [pos]
    pm.update_price = MagicMock()
    pm.should_exit.return_value = exit_reason
    pm.close = MagicMock()
    return pm


def _make_broker(bid: float, ask: float):
    """Broker returning a quote with given bid/ask; place_option_order succeeds."""
    quote = MagicMock()
    quote.bid = bid
    quote.ask = ask
    quote.mid = (bid + ask) / 2

    order_result = MagicMock()
    order_result.order_id = "exit-order-001"

    broker = MagicMock()
    broker.get_option_quote = AsyncMock(return_value=quote)
    broker.place_option_order = AsyncMock(return_value=order_result)
    return broker


def _make_journal():
    j = MagicMock()
    j.record_exit = AsyncMock()
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
    monitor_positions() must use bid as exit_price for P&L and journal recording.
    The midpoint must only be used for exit condition evaluation.
    """

    @pytest.mark.asyncio
    async def test_meta_scenario_journal_uses_bid_not_mid(self):
        """
        2026-06-03 META scenario:
        entry=$0.51, exit bid=$0.33 ask=$0.45 mid=$0.39
        Old: exit_price=$0.39, pnl=-$12  (wrong)
        New: exit_price=$0.33, pnl=-$18  (correct — bid = limit price)
        """
        from scripts.session_runner import monitor_positions

        pos = _make_pos(entry_price=0.51)
        pm = _make_pm(pos, exit_reason="trailing_stop")
        broker = _make_broker(bid=0.33, ask=0.45)
        journal = _make_journal()
        risk = _make_risk()

        await monitor_positions(
            broker=broker, pm=pm, journal=journal, risk=risk,
            now=datetime(2026, 6, 3, 12, 29, tzinfo=ET),
            dry_run=False, settings=_make_settings(),
        )

        journal.record_exit.assert_awaited_once()
        call_kw = journal.record_exit.call_args.kwargs
        # Must use bid, not mid (0.39)
        assert call_kw["exit_price"] == pytest.approx(0.33), (
            f"Expected exit_price=0.33 (bid), got {call_kw['exit_price']}"
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
        from scripts.session_runner import monitor_positions

        pos = _make_pos(
            entry_price=0.21,
            symbol="AMZN",
            option_symbol="AMZN260603P00247500",
            journal_id=307,
        )
        pm = _make_pm(pos, exit_reason="trailing_stop")
        broker = _make_broker(bid=0.13, ask=0.18)
        journal = _make_journal()
        risk = _make_risk()

        await monitor_positions(
            broker=broker, pm=pm, journal=journal, risk=risk,
            now=datetime(2026, 6, 3, 12, 2, tzinfo=ET),
            dry_run=False, settings=_make_settings(),
        )

        call_kw = journal.record_exit.call_args.kwargs
        assert call_kw["exit_price"] == pytest.approx(0.13)
        assert call_kw["realized_pnl"] == pytest.approx(-8.0)

    @pytest.mark.asyncio
    async def test_bid_and_ask_still_recorded_as_separate_fields(self):
        """exit_bid and exit_ask must still be passed for spread analysis."""
        from scripts.session_runner import monitor_positions

        pos = _make_pos(entry_price=0.51)
        pm = _make_pm(pos, exit_reason="trailing_stop")
        broker = _make_broker(bid=0.33, ask=0.45)
        journal = _make_journal()

        await monitor_positions(
            broker=broker, pm=pm, journal=journal, risk=_make_risk(),
            now=datetime(2026, 6, 3, 12, 29, tzinfo=ET),
            dry_run=False,
        )

        call_kw = journal.record_exit.call_args.kwargs
        assert call_kw["exit_bid"] == pytest.approx(0.33)
        assert call_kw["exit_ask"] == pytest.approx(0.45)

    @pytest.mark.asyncio
    async def test_fallback_to_mid_when_bid_is_zero(self):
        """When bid=0 (no market), fall back to midpoint for exit_price."""
        from scripts.session_runner import monitor_positions

        pos = _make_pos(entry_price=2.00)
        pm = _make_pm(pos, exit_reason="trailing_stop")
        # bid=0 simulates a stale/empty quote
        broker = _make_broker(bid=0.0, ask=4.00)
        journal = _make_journal()

        await monitor_positions(
            broker=broker, pm=pm, journal=journal, risk=_make_risk(),
            now=datetime(2026, 6, 3, 12, 0, tzinfo=ET),
            dry_run=False,
        )

        call_kw = journal.record_exit.call_args.kwargs
        # mid = (0 + 4) / 2 = 2.0; should use mid as fallback
        assert call_kw["exit_price"] == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_pm_close_receives_bid_based_exit_price(self):
        """pm.close() must be called with bid price so position records actual execution price."""
        from scripts.session_runner import monitor_positions

        pos = _make_pos(entry_price=0.51)
        pm = _make_pm(pos, exit_reason="trailing_stop")
        broker = _make_broker(bid=0.33, ask=0.45)

        await monitor_positions(
            broker=broker, pm=pm, journal=_make_journal(), risk=_make_risk(),
            now=datetime(2026, 6, 3, 12, 29, tzinfo=ET),
            dry_run=False,
        )

        pm.close.assert_called_once()
        close_args = pm.close.call_args.args
        # pm.close(option_symbol, exit_price, pnl)
        assert close_args[1] == pytest.approx(0.33), (
            f"Expected pm.close exit_price=0.33 (bid), got {close_args[1]}"
        )

    @pytest.mark.asyncio
    async def test_stop_loss_also_uses_bid(self):
        """stop_loss exits are also bid-priced — not mid."""
        from scripts.session_runner import monitor_positions

        pos = _make_pos(entry_price=1.00)
        pm = _make_pm(pos, exit_reason="stop_loss")
        broker = _make_broker(bid=0.40, ask=0.70)  # mid=0.55
        journal = _make_journal()

        await monitor_positions(
            broker=broker, pm=pm, journal=journal, risk=_make_risk(),
            now=datetime(2026, 6, 3, 10, 0, tzinfo=ET),
            dry_run=False,
        )

        call_kw = journal.record_exit.call_args.kwargs
        assert call_kw["exit_price"] == pytest.approx(0.40)
        assert call_kw["realized_pnl"] == pytest.approx(-60.0)  # (0.40-1.00)*100

    @pytest.mark.asyncio
    async def test_pnl_reconciliation_matches_broker_execution(self):
        """
        Reconciliation test: journal pnl must equal broker execution pnl.
        Broker executes at bid (limit order); journal must reflect that.

        Entry fill: $0.21 (via Alpaca, confirmed)
        Exit quote: bid=$0.13, ask=$0.18
        Broker execution: $0.13 (limit=bid)
        Expected journal pnl: (0.13 - 0.21) * 100 = -$8.00
        """
        from scripts.session_runner import monitor_positions

        pos = _make_pos(entry_price=0.21, symbol="AMZN",
                        option_symbol="AMZN260603P00247500")
        pm = _make_pm(pos, exit_reason="trailing_stop")
        broker = _make_broker(bid=0.13, ask=0.18)
        journal = _make_journal()

        await monitor_positions(
            broker=broker, pm=pm, journal=journal, risk=_make_risk(),
            now=datetime(2026, 6, 3, 12, 2, tzinfo=ET),
            dry_run=False, settings=_make_settings(),
        )

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
        """EOD exit P&L must use bid, not mid."""
        from scripts.session_runner import eod_liquidate

        pos = _make_pos(entry_price=2.00)
        pm = MagicMock()
        pm.open_positions.return_value = [pos]
        pm.close = MagicMock()

        broker = _make_broker(bid=1.20, ask=1.80)  # mid=1.50
        journal = _make_journal()
        risk = _make_risk()

        await eod_liquidate(
            broker=broker, pm=pm, journal=journal, risk=risk,
            now=datetime(2026, 6, 3, 12, 30, tzinfo=ET),
            dry_run=False,
        )

        call_kw = journal.record_exit.call_args.kwargs
        # Must use bid=1.20, not mid=1.50
        assert call_kw["exit_price"] == pytest.approx(1.20), (
            f"Expected eod exit_price=1.20 (bid), got {call_kw['exit_price']}"
        )
        assert call_kw["realized_pnl"] == pytest.approx(-80.0), (
            f"Expected eod pnl=-80.0 (bid-based), got {call_kw['realized_pnl']}"
        )

    @pytest.mark.asyncio
    async def test_eod_fallback_to_mid_when_bid_zero(self):
        """EOD exit falls back to mid when bid=0."""
        from scripts.session_runner import eod_liquidate

        pos = _make_pos(entry_price=2.00)
        pm = MagicMock()
        pm.open_positions.return_value = [pos]
        pm.close = MagicMock()

        broker = _make_broker(bid=0.0, ask=3.00)  # mid=1.50
        journal = _make_journal()

        await eod_liquidate(
            broker=broker, pm=pm, journal=journal, risk=_make_risk(),
            now=datetime(2026, 6, 3, 12, 30, tzinfo=ET),
            dry_run=False,
        )

        call_kw = journal.record_exit.call_args.kwargs
        # mid = (0 + 3) / 2 = 1.50; use as fallback
        assert call_kw["exit_price"] == pytest.approx(1.50)
