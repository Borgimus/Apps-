"""
Focused replay validation of the 2026-06-03 AMZN/SMH stale-cancel scenario.

Uses real FillTracker and RiskManager instances (not mocks) so the counter
interactions are validated through the actual state machine.

Acceptance criteria verified:
  A1  stale cancel passes risk and decrements pending_entries
  A2  cancel 422 triggers order status re-fetch (get_order_status called)
  A3  filled order is handled as filled, not dead (journal.record_fill not record_cancellation)
  A4  position opens correctly (pm.open called with correct entry price / symbol)
  A5  journal records entry fill (record_fill called with journal_id + fill_price)
  A6  exit uses bid-side fill price and P&L (not midpoint)
  A7  ledger includes the AMZN trade after add_session
  A8  pending_entries returns to 0 after all orders resolve
  A9  no orphan position created (pm.open called exactly once)
  A10 COIN/NVDA-style later signals are NOT blocked by false capacity exhaustion

All tests are offline (no DB, no broker network, no file I/O in normal cases).
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from app.brokers.broker_interface import OrderStatus
from app.trading.fill_tracker import FillTracker
from app.risk.risk_manager import RiskManager

ET = ZoneInfo("America/New_York")


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_risk_settings(max_trades: int = 3) -> MagicMock:
    """Minimal settings mock that satisfies RiskManager without needing a full Settings object."""
    s = MagicMock()
    s.is_kill_switch_active.return_value = False
    s.live_trading_enabled = False
    s.risk.max_trades_per_day = max_trades
    s.risk.max_daily_loss = 0.10
    s.risk.max_risk_per_trade = 0.02
    s.risk.allow_earnings_trades = True
    return s


def _real_risk(max_trades: int = 3) -> RiskManager:
    """Return a real RiskManager with test settings."""
    risk = RiskManager(settings=_make_risk_settings(max_trades))
    risk._starting_equity = Decimal("100000")
    return risk


def _ft(max_age_minutes: int = 2) -> FillTracker:
    return FillTracker(max_age_minutes=max_age_minutes)


def _pm():
    pm = MagicMock()
    pm.has_position = MagicMock(return_value=False)
    pm.open = MagicMock()
    pm._positions = {}
    return pm


def _journal():
    j = MagicMock()
    j.record_fill = AsyncMock()
    j.record_cancellation = AsyncMock()
    j.log_event = AsyncMock()
    j.commit = AsyncMock()
    return j


def _broker_cancel_ok() -> MagicMock:
    """Normal stale cancel: cancel() succeeds; status is still NEW."""
    resp = MagicMock()
    resp.status = OrderStatus.NEW
    resp.filled_quantity = 0
    resp.filled_price = None
    b = MagicMock()
    b.cancel_order = AsyncMock()
    b.get_order_status = AsyncMock(return_value=resp)
    return b


def _broker_422_filled(fill_price: float = 0.21) -> MagicMock:
    """Stale cancel raises 422; get_order_status returns FILLED."""
    fill_resp = MagicMock()
    fill_resp.status = OrderStatus.FILLED
    fill_resp.filled_quantity = 1
    fill_resp.filled_price = fill_price
    b = MagicMock()
    b.cancel_order = AsyncMock(side_effect=RuntimeError("HTTP 422: unprocessable entity"))
    b.get_order_status = AsyncMock(return_value=fill_resp)
    return b


def _stale(minutes: int = 5) -> datetime:
    return datetime.now() - timedelta(minutes=minutes)


def _register_smh(ft: FillTracker, placed_at: datetime | None = None) -> None:
    ft.register(
        order_id="SMH-306",
        journal_id=306,
        option_symbol="SMH260605C00637500",
        symbol="SMH",
        strategy_id="vwap_reclaim",
        direction="LONG",
        quantity=1,
        limit_price=9.72,
        placed_at=placed_at or _stale(),
    )


def _register_amzn(ft: FillTracker, placed_at: datetime | None = None) -> None:
    ft.register(
        order_id="AMZN-307",
        journal_id=307,
        option_symbol="AMZN260603P00247500",
        symbol="AMZN",
        strategy_id="orb",
        direction="SHORT",
        quantity=1,
        limit_price=0.21,
        placed_at=placed_at or _stale(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# A1  SMH normal stale cancel — pending_entries decrements
# ─────────────────────────────────────────────────────────────────────────────

class TestA1_StaleCancelDecrementsRisk:

    @pytest.mark.asyncio
    async def test_smh_stale_cancel_decrements_pending_entries(self):
        """
        A1: SMH placed (pending=1) → stale cancel → risk.record_entry_cancelled()
        called → pending returns to 0.
        """
        risk = _real_risk()
        ft = _ft()

        risk.record_entry_pending()
        assert risk.pending_entries == 1

        _register_smh(ft)

        await ft.poll(_broker_cancel_ok(), _pm(), _journal(), datetime.now(), risk=risk)

        assert risk.pending_entries == 0, (
            f"pending_entries should be 0 after stale cancel, got {risk.pending_entries}"
        )
        assert risk.entries_today == 0, "stale cancel must not increment entries_today"
        assert ft.count() == 0

    @pytest.mark.asyncio
    async def test_two_stale_cancels_both_decrement(self):
        """
        A1 extended: SMH (#306) + AMZN (#307) stale-cancelled.
        Both must decrement; final pending=0.
        This is the exact counter state that would have allowed COIN and NVDA.
        """
        risk = _real_risk()
        ft = _ft()

        # SMH placed
        risk.record_entry_pending()
        _register_smh(ft)

        # AMZN placed (different symbol so no dedup block)
        risk.record_entry_pending()
        _register_amzn(ft)

        assert risk.pending_entries == 2

        # Both stale-cancel normally (no 422 for this test variant)
        cancel_ok_broker = MagicMock()
        cancel_ok_broker.cancel_order = AsyncMock()
        cancel_ok_broker.get_order_status = AsyncMock(return_value=MagicMock(
            status=OrderStatus.CANCELLED,
            filled_quantity=0,
            filled_price=None,
        ))

        await ft.poll(cancel_ok_broker, _pm(), _journal(), datetime.now(), risk=risk)

        assert risk.pending_entries == 0, (
            f"Both stale cancels must decrement; got pending={risk.pending_entries}"
        )
        assert risk.entries_today == 0
        assert ft.count() == 0


# ─────────────────────────────────────────────────────────────────────────────
# A2 + A3  AMZN 422 → re-fetch → fill detected
# ─────────────────────────────────────────────────────────────────────────────

class TestA2A3_CancelRecheck:

    @pytest.mark.asyncio
    async def test_422_triggers_get_order_status(self):
        """
        A2: When cancel_order raises an exception (HTTP 422), FillTracker must
        call get_order_status to re-check the actual order state.
        """
        risk = _real_risk()
        ft = _ft()
        risk.record_entry_pending()
        _register_amzn(ft)

        broker = _broker_422_filled()
        await ft.poll(broker, _pm(), _journal(), datetime.now(), risk=risk)

        broker.get_order_status.assert_awaited_once_with("AMZN-307")

    @pytest.mark.asyncio
    async def test_422_fill_detected_not_dead(self):
        """
        A3: After 422 and re-check confirms FILLED:
        - journal.record_fill called (not record_cancellation)
        - fills counter incremented
        """
        risk = _real_risk()
        ft = _ft()
        risk.record_entry_pending()
        _register_amzn(ft)

        journal = _journal()
        fills = await ft.poll(_broker_422_filled(), _pm(), journal, datetime.now(), risk=risk)

        assert fills == 1, f"Expected 1 fill detected, got {fills}"
        journal.record_fill.assert_awaited_once()
        journal.record_cancellation.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────
# A4  Position opens correctly
# ─────────────────────────────────────────────────────────────────────────────

class TestA4_PositionOpens:

    @pytest.mark.asyncio
    async def test_position_opens_with_actual_fill_price(self):
        """
        A4: pm.open() called once with the broker's reported fill price
        (0.21 as confirmed by Alpaca), not the limit price.
        """
        risk = _real_risk()
        ft = _ft()
        risk.record_entry_pending()
        _register_amzn(ft)

        pm = _pm()
        await ft.poll(_broker_422_filled(fill_price=0.21), pm, _journal(), datetime.now(), risk=risk)

        pm.open.assert_called_once()
        kw = pm.open.call_args.kwargs
        assert kw["option_symbol"] == "AMZN260603P00247500"
        assert kw["symbol"] == "AMZN"
        assert kw["strategy_id"] == "orb"
        assert kw["direction"] == "SHORT"
        assert kw["quantity"] == 1
        assert kw["entry_price"] == pytest.approx(0.21)

    @pytest.mark.asyncio
    async def test_position_not_opened_for_stale_cancel(self):
        """
        A4 counter-case: SMH normal stale cancel must NOT open a position.
        """
        risk = _real_risk()
        ft = _ft()
        risk.record_entry_pending()
        _register_smh(ft)

        pm = _pm()
        await ft.poll(_broker_cancel_ok(), pm, _journal(), datetime.now(), risk=risk)

        pm.open.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# A5  Journal records entry fill
# ─────────────────────────────────────────────────────────────────────────────

class TestA5_JournalFill:

    @pytest.mark.asyncio
    async def test_journal_record_fill_called_with_correct_fields(self):
        """
        A5: journal.record_fill must be awaited with journal_id=307,
        fill_price=0.21, filled_quantity=1 — matching the broker-confirmed fill.
        """
        risk = _real_risk()
        ft = _ft()
        risk.record_entry_pending()
        _register_amzn(ft)

        journal = _journal()
        await ft.poll(_broker_422_filled(fill_price=0.21), _pm(), journal, datetime.now(), risk=risk)

        journal.record_fill.assert_awaited_once()
        kw = journal.record_fill.call_args.kwargs
        assert kw["journal_id"] == 307
        assert kw["fill_price"] == pytest.approx(0.21)
        assert kw["filled_quantity"] == 1

    @pytest.mark.asyncio
    async def test_journal_cancellation_not_called_for_422_fill(self):
        """
        A5 counter-case: record_cancellation must NOT be called when 422
        resolves to a fill.
        """
        risk = _real_risk()
        ft = _ft()
        risk.record_entry_pending()
        _register_amzn(ft)

        journal = _journal()
        await ft.poll(_broker_422_filled(), _pm(), journal, datetime.now(), risk=risk)

        journal.record_cancellation.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────
# A6  Exit uses bid-side price and P&L
# ─────────────────────────────────────────────────────────────────────────────

class TestA6_ExitBidPricing:

    @pytest.mark.asyncio
    async def test_amzn_trailing_stop_uses_bid_not_mid(self):
        """
        A6: AMZN exit bid=$0.13 ask=$0.18 mid=$0.155.
        Old P&L: (0.155 - 0.21) * 100 = -$5.50  (wrong — midpoint)
        New P&L: (0.13  - 0.21) * 100 = -$8.00  (correct — bid)
        """
        from scripts.session_runner import monitor_positions

        pos = MagicMock()
        pos.symbol = "AMZN"
        pos.option_symbol = "AMZN260603P00247500"
        pos.entry_price = 0.21
        pos.quantity = 1
        pos.strategy_id = "orb"
        pos.journal_id = 307
        pos.entry_time = datetime(2026, 6, 3, 11, 39, tzinfo=ET)

        pm = MagicMock()
        pm.open_positions.return_value = [pos]
        pm.update_price = MagicMock()
        pm.should_exit.return_value = "trailing_stop"
        pm.close = MagicMock()

        quote = MagicMock()
        quote.bid = 0.13
        quote.ask = 0.18
        quote.mid = (0.13 + 0.18) / 2  # 0.155

        order_result = MagicMock()
        order_result.order_id = "exit-amzn-001"

        broker = MagicMock()
        broker.get_option_quote = AsyncMock(return_value=quote)
        broker.place_option_order = AsyncMock(return_value=order_result)

        risk = MagicMock()
        risk.record_exit = MagicMock()

        journal = MagicMock()
        journal.record_exit = AsyncMock()
        journal.log_event = AsyncMock()
        journal.commit = AsyncMock()

        settings = MagicMock()
        settings.risk.max_spread_pct = 0.50

        await monitor_positions(
            broker=broker, pm=pm, journal=journal, risk=risk,
            now=datetime(2026, 6, 3, 12, 2, tzinfo=ET),
            dry_run=False, settings=settings,
        )

        journal.record_exit.assert_awaited_once()
        kw = journal.record_exit.call_args.kwargs

        # A6: exit_price must be bid (0.13), not mid (0.155)
        assert kw["exit_price"] == pytest.approx(0.13), (
            f"exit_price should be bid=0.13, got {kw['exit_price']:.4f}. "
            f"Mid was 0.155 — ensure Bug C fix is in place."
        )
        assert kw["realized_pnl"] == pytest.approx(-8.0), (
            f"P&L should be -8.00 (bid-based), got {kw['realized_pnl']:.2f}. "
            f"Mid-based would be -5.50."
        )

    @pytest.mark.asyncio
    async def test_meta_trailing_stop_uses_bid_not_mid(self):
        """
        A6 META scenario: bid=$0.33 ask=$0.45 mid=$0.39 entry=$0.51.
        Journal must record exit_price=0.33 pnl=-18.00 (not -12.00).
        """
        from scripts.session_runner import monitor_positions

        pos = MagicMock()
        pos.symbol = "META"
        pos.option_symbol = "META260603P00605000"
        pos.entry_price = 0.51
        pos.quantity = 1
        pos.strategy_id = "vwap_reclaim"
        pos.journal_id = 308
        pos.entry_time = datetime(2026, 6, 3, 12, 20, tzinfo=ET)

        pm = MagicMock()
        pm.open_positions.return_value = [pos]
        pm.update_price = MagicMock()
        pm.should_exit.return_value = "trailing_stop"
        pm.close = MagicMock()

        quote = MagicMock()
        quote.bid = 0.33
        quote.ask = 0.45
        quote.mid = (0.33 + 0.45) / 2  # 0.39

        order_result = MagicMock()
        order_result.order_id = "exit-meta-001"

        broker = MagicMock()
        broker.get_option_quote = AsyncMock(return_value=quote)
        broker.place_option_order = AsyncMock(return_value=order_result)

        risk = MagicMock()
        risk.record_exit = MagicMock()

        journal = MagicMock()
        journal.record_exit = AsyncMock()
        journal.log_event = AsyncMock()
        journal.commit = AsyncMock()

        await monitor_positions(
            broker=broker, pm=pm, journal=journal, risk=risk,
            now=datetime(2026, 6, 3, 12, 29, tzinfo=ET),
            dry_run=False,
        )

        kw = journal.record_exit.call_args.kwargs
        assert kw["exit_price"] == pytest.approx(0.33)
        assert kw["realized_pnl"] == pytest.approx(-18.0)


# ─────────────────────────────────────────────────────────────────────────────
# A7  Ledger includes the AMZN trade
# ─────────────────────────────────────────────────────────────────────────────

class TestA7_LedgerIncludesAMZN:

    def test_ledger_includes_amzn_orb_trade_after_add_session(self):
        """
        A7: EvaluationLedger.add_session() with a DailyReport that includes
        the AMZN ORB trade must produce a ledger entry with:
        - total_trades = 1 (AMZN fill only; SMH stale-cancelled excluded)
        - orb strategy in by_strategy with trades=1, losses=1
        - realized_pnl = -8.00 (bid-based exit, not midpoint)
        """
        from app.evaluation.ledger import EvaluationLedger
        from app.evaluation.daily_report import DailyReport, StrategyStats

        report = DailyReport(
            date="2026-06-03",
            session_start="10:30",
            session_end="12:30",
            trades_submitted=2,   # SMH + AMZN
            trades_filled=1,      # AMZN only
            trades_cancelled=2,   # SMH stale + AMZN stale (but AMZN filled via recheck)
            trades_rejected=0,
            realized_pnl=-8.0,    # AMZN: (0.13 - 0.21) * 100 = -8.00 (bid-based)
            unrealized_pnl=0.0,
            max_drawdown=8.0,
            slippage_total=0.0,
            spread_cost_estimate=0.0,
            api_errors=0,
            kill_switch_events=0,
            by_strategy=[
                StrategyStats(
                    strategy_id="orb",
                    wins=0,
                    losses=1,
                    realized_pnl=-8.0,
                ),
            ],
        )

        ledger = EvaluationLedger(ledger_file="/tmp/test_replay_ledger.json")
        entry = ledger.add_session(report)

        # A7: AMZN trade recorded in ledger
        assert entry.total_trades == 1, f"Expected 1 trade (AMZN), got {entry.total_trades}"
        assert entry.wins == 0
        assert entry.losses == 1
        assert entry.realized_pnl == pytest.approx(-8.0), (
            f"Expected pnl=-8.00 (bid-based), got {entry.realized_pnl}. "
            f"Mid-based would be -5.50."
        )

        # ORB strategy correctly attributed
        assert "orb" in entry.by_strategy, "orb strategy must appear in ledger by_strategy"
        orb = entry.by_strategy["orb"]
        assert orb["trades"] == 1
        assert orb["losses"] == 1
        assert orb["pnl"] == pytest.approx(-8.0)

        # SMH is NOT in the ledger (stale-cancelled without fill)
        assert "vwap_reclaim" not in entry.by_strategy or \
               entry.by_strategy.get("vwap_reclaim", {}).get("trades", 0) == 0, \
            "SMH stale cancel must not contribute a trade to the ledger"

    def test_ledger_two_trade_session_amzn_plus_meta(self):
        """
        A7 extended: session with AMZN orb (-$8) + META vwap_reclaim (-$18).
        Both must appear in ledger with correct bid-based P&L.
        Total = -$26 (not -$17.50 as recorded with midpoint pricing).
        """
        from app.evaluation.ledger import EvaluationLedger
        from app.evaluation.daily_report import DailyReport, StrategyStats

        report = DailyReport(
            date="2026-06-03",
            session_start="10:30",
            session_end="12:30",
            trades_submitted=3,
            trades_filled=2,
            trades_cancelled=2,
            trades_rejected=15,
            realized_pnl=-26.0,   # -8 (AMZN) + -18 (META), bid-based
            unrealized_pnl=0.0,
            max_drawdown=26.0,
            slippage_total=0.0,
            spread_cost_estimate=0.0,
            api_errors=0,
            kill_switch_events=0,
            by_strategy=[
                StrategyStats(strategy_id="orb", wins=0, losses=1, realized_pnl=-8.0),
                StrategyStats(strategy_id="vwap_reclaim", wins=0, losses=1, realized_pnl=-18.0),
            ],
        )

        ledger = EvaluationLedger(ledger_file="/tmp/test_replay_ledger_2.json")
        entry = ledger.add_session(report)

        assert entry.total_trades == 2
        assert entry.losses == 2
        assert entry.realized_pnl == pytest.approx(-26.0)
        assert entry.by_strategy["orb"]["pnl"] == pytest.approx(-8.0)
        assert entry.by_strategy["vwap_reclaim"]["pnl"] == pytest.approx(-18.0)


# ─────────────────────────────────────────────────────────────────────────────
# A8  pending_entries returns to 0
# ─────────────────────────────────────────────────────────────────────────────

class TestA8_PendingReturnsToZero:

    @pytest.mark.asyncio
    async def test_pending_zero_after_smh_cancel_and_amzn_fill(self):
        """
        A8: Full SMH + AMZN sequence.
        SMH placed (pending=1) → stale cancel → pending=0.
        AMZN placed (pending=1) → 422 fill → entries=1, pending=0.
        Final state: entries_today=1, pending_entries=0.
        """
        risk = _real_risk()

        # ── Step 1: SMH order placed and stale-cancelled ──────────────────
        ft_smh = _ft(max_age_minutes=1)
        risk.record_entry_pending()
        _register_smh(ft_smh, placed_at=_stale(5))

        await ft_smh.poll(_broker_cancel_ok(), _pm(), _journal(), datetime.now(), risk=risk)

        assert risk.pending_entries == 0, f"After SMH stale cancel: pending={risk.pending_entries}"
        assert risk.entries_today == 0

        # ── Step 2: AMZN order placed; fills via 422 re-check ────────────
        ft_amzn = _ft(max_age_minutes=1)
        risk.record_entry_pending()
        _register_amzn(ft_amzn, placed_at=_stale(2))

        pm_amzn = _pm()
        await ft_amzn.poll(_broker_422_filled(0.21), pm_amzn, _journal(), datetime.now(), risk=risk)

        # A8: both counters in correct final state
        assert risk.pending_entries == 0, (
            f"After AMZN 422 fill: pending={risk.pending_entries} (expected 0)"
        )
        assert risk.entries_today == 1, (
            f"After AMZN fill: entries={risk.entries_today} (expected 1)"
        )
        assert pm_amzn.open.call_count == 1

    @pytest.mark.asyncio
    async def test_pending_zero_after_full_three_order_sequence(self):
        """
        A8 full: SMH cancel + AMZN 422-fill + META normal fill.
        entries=2, pending=0 — exactly as it should have been on 2026-06-03.
        """
        risk = _real_risk(max_trades=3)

        # SMH stale cancel
        ft1 = _ft(max_age_minutes=1)
        risk.record_entry_pending()
        _register_smh(ft1, placed_at=_stale(5))
        await ft1.poll(_broker_cancel_ok(), _pm(), _journal(), datetime.now(), risk=risk)

        assert risk.pending_entries == 0

        # AMZN 422 fill
        ft2 = _ft(max_age_minutes=1)
        risk.record_entry_pending()
        _register_amzn(ft2, placed_at=_stale(2))
        await ft2.poll(_broker_422_filled(0.21), _pm(), _journal(), datetime.now(), risk=risk)

        assert risk.pending_entries == 0
        assert risk.entries_today == 1

        # META normal fill: simulate session_runner sequence
        risk.record_entry_pending()
        risk.record_entry_filled()   # FillTracker calls this after proper fill

        # A8 final state
        assert risk.pending_entries == 0, f"pending={risk.pending_entries}"
        assert risk.entries_today == 2, f"entries={risk.entries_today}"


# ─────────────────────────────────────────────────────────────────────────────
# A9  No orphan position
# ─────────────────────────────────────────────────────────────────────────────

class TestA9_NoOrphanPosition:

    @pytest.mark.asyncio
    async def test_no_orphan_position_after_422_fill(self):
        """
        A9: pm.open() called exactly once; ft.count() == 0 after 422 fill.
        Previously: fill was dropped, pm.open() never called, position became
        an orphan until Reconciler ran 22 min later.
        """
        risk = _real_risk()
        ft = _ft()
        risk.record_entry_pending()
        _register_amzn(ft)

        pm = _pm()
        fills = await ft.poll(_broker_422_filled(0.21), pm, _journal(), datetime.now(), risk=risk)

        # A9: exactly one position opened, no lingering pending order
        assert fills == 1
        assert pm.open.call_count == 1, (
            f"pm.open called {pm.open.call_count} times; expected exactly 1"
        )
        assert ft.count() == 0, f"FillTracker still has {ft.count()} pending orders after fill"
        assert ft.has_pending_for_symbol("AMZN") is False

    @pytest.mark.asyncio
    async def test_reconciler_not_needed_for_fill_recovery(self):
        """
        A9 confirmation: the fix means Reconciler is no longer the primary
        recovery path.  With the fix, the fill is recorded immediately.
        We verify this by checking that pm.open() is called in the same poll
        cycle as the 422 (not deferred).
        """
        risk = _real_risk()
        ft = _ft()
        risk.record_entry_pending()
        _register_amzn(ft)

        pm = _pm()
        journal = _journal()

        # Single poll call — if pm.open is called here, no deferred recovery needed
        fills = await ft.poll(_broker_422_filled(0.21), pm, journal, datetime.now(), risk=risk)

        # Position was opened in this poll cycle
        assert fills == 1
        pm.open.assert_called_once()
        journal.record_fill.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────────────
# A10  COIN/NVDA not blocked by false capacity exhaustion
# ─────────────────────────────────────────────────────────────────────────────

class TestA10_CoinNvdaNotBlocked:

    @pytest.mark.asyncio
    async def test_capacity_correct_after_smh_amzn_meta(self):
        """
        A10: After SMH stale-cancel + AMZN 422-fill + META normal fill,
        capacity = entries(2) + pending(0) = 2.
        With max_trades=3: 2 < 3 → COIN and NVDA slots are available.

        Before the fix: SMH and AMZN leaked their pending slots, so at META fill:
        entries=1, pending=2 → capacity=3/3 → COIN and NVDA blocked.
        """
        risk = _real_risk(max_trades=3)

        # SMH stale cancel (no 422)
        ft1 = _ft(max_age_minutes=1)
        risk.record_entry_pending()
        _register_smh(ft1, placed_at=_stale(5))
        await ft1.poll(_broker_cancel_ok(), _pm(), _journal(), datetime.now(), risk=risk)

        # AMZN 422 fill
        ft2 = _ft(max_age_minutes=1)
        risk.record_entry_pending()
        _register_amzn(ft2, placed_at=_stale(2))
        await ft2.poll(_broker_422_filled(0.21), _pm(), _journal(), datetime.now(), risk=risk)

        # META normal fill
        risk.record_entry_pending()
        risk.record_entry_filled()

        # ── Verify capacity ───────────────────────────────────────────────
        capacity = risk.entries_today + risk.pending_entries
        assert capacity == 2, (
            f"capacity={capacity}; before fix it was 3 (SMH+AMZN leaked)."
        )
        assert capacity < 3, (
            "Capacity must be < max_trades(3) so COIN/NVDA can still be placed."
        )

        # Direct counter verification
        assert risk.entries_today == 2, f"entries_today={risk.entries_today}"
        assert risk.pending_entries == 0, f"pending_entries={risk.pending_entries}"

    @pytest.mark.asyncio
    async def test_coin_capacity_check_passes(self):
        """
        A10 capacity gate: after SMH+AMZN+META with correct counters,
        the capacity check (entries+pending < max_trades) passes for COIN.
        """
        risk = _real_risk(max_trades=3)

        # Replay same sequence
        ft1 = _ft(max_age_minutes=1)
        risk.record_entry_pending()
        _register_smh(ft1, placed_at=_stale(5))
        await ft1.poll(_broker_cancel_ok(), _pm(), _journal(), datetime.now(), risk=risk)

        ft2 = _ft(max_age_minutes=1)
        risk.record_entry_pending()
        _register_amzn(ft2, placed_at=_stale(2))
        await ft2.poll(_broker_422_filled(0.21), _pm(), _journal(), datetime.now(), risk=risk)

        risk.record_entry_pending()
        risk.record_entry_filled()  # META filled

        # Simulate COIN capacity check (what _check_max_trades_per_day does)
        capacity_used = risk.entries_today + risk.pending_entries
        max_trades = risk._s.risk.max_trades_per_day
        coin_would_pass = capacity_used < max_trades

        assert coin_would_pass, (
            f"COIN must not be blocked: capacity={capacity_used} max={max_trades}. "
            f"Before fix: capacity was 3/3."
        )

    @pytest.mark.asyncio
    async def test_before_fix_capacity_would_have_been_3_of_3(self):
        """
        A10 regression reference: documents what the pre-fix behavior WAS.
        Without the fix, pending_entries would have been 2 after SMH+AMZN stale cancel,
        making capacity = entries(1) + pending(2) = 3/3 after META fill.

        This test simulates the buggy counter state to confirm the fix's impact.
        """
        # Simulate pre-fix state: pending_entries NOT decremented on stale cancel
        risk = _real_risk(max_trades=3)

        # SMH placed — pretend stale cancel happens but pending NOT decremented (bug)
        risk._pending_entries += 1  # record_entry_pending

        # AMZN placed — pretend 422 drop happens but pending NOT decremented (bug)
        risk._pending_entries += 1  # record_entry_pending; pending=2

        # META placed and filled
        risk._pending_entries += 1  # pending=3
        risk.record_entry_filled()  # entries=1, pending=2

        buggy_capacity = risk.entries_today + risk.pending_entries
        assert buggy_capacity == 3, (
            f"Buggy capacity should be 3; got {buggy_capacity}"
        )
        # Confirm this would have blocked COIN
        assert buggy_capacity >= 3, "Pre-fix: COIN would have been blocked"


# ─────────────────────────────────────────────────────────────────────────────
# Full end-to-end scenario replay (all criteria in one sequence)
# ─────────────────────────────────────────────────────────────────────────────

class TestFullScenarioReplay:

    @pytest.mark.asyncio
    async def test_2026_06_03_amzn_smh_full_replay(self):
        """
        Full 2026-06-03 AMZN/SMH replay in chronological order.

        Sequence:
          11:27 ET: SMH order placed (pending=1)
          11:29 ET: SMH stale-cancelled → pending=0   [A1]
          11:37 ET: AMZN order placed (pending=1)
          11:39 ET: AMZN fills at Alpaca
          11:39 ET: Stale cancel fires → 422 → re-check → FILLED  [A2, A3]
          .......: Position opens correctly  [A4]
          .......: Journal fill recorded  [A5]
          .......: pending=0, entries=1  [A8]
          .......: No orphan  [A9]
          12:02 ET: Exit trailing stop bid=$0.13 → pnl=-$8.00  [A6]
          Session end: ledger includes AMZN  [A7]
          COIN/NVDA check: capacity=1+0=1 < 3  [A10]
        """
        risk = _real_risk(max_trades=3)

        # ── 11:27 ET: SMH placed ──────────────────────────────────────────
        ft_smh = _ft(max_age_minutes=1)
        risk.record_entry_pending()
        _register_smh(ft_smh, placed_at=_stale(5))

        # ── 11:29 ET: SMH stale-cancelled ────────────────────────────────
        pm_smh = _pm()
        journal_smh = _journal()
        await ft_smh.poll(_broker_cancel_ok(), pm_smh, journal_smh, datetime.now(), risk=risk)

        assert risk.pending_entries == 0, "A1: SMH cancel must free pending slot"
        pm_smh.open.assert_not_called()                                            # A9 counter
        journal_smh.record_cancellation.assert_awaited_once()

        # ── 11:37 ET: AMZN placed ────────────────────────────────────────
        ft_amzn = _ft(max_age_minutes=1)
        risk.record_entry_pending()
        _register_amzn(ft_amzn, placed_at=_stale(2))

        assert risk.pending_entries == 1

        # ── 11:39 ET: Stale cancel fires 7s after fill → 422 → fill detected ──
        broker_amzn = _broker_422_filled(fill_price=0.21)
        pm_amzn = _pm()
        journal_amzn = _journal()
        fills = await ft_amzn.poll(
            broker_amzn, pm_amzn, journal_amzn, datetime.now(), risk=risk
        )

        assert fills == 1,                    "A3: fill must be detected"
        broker_amzn.get_order_status.assert_awaited_once()    # A2
        journal_amzn.record_fill.assert_awaited_once()        # A5
        journal_amzn.record_cancellation.assert_not_awaited() # A3
        pm_amzn.open.assert_called_once()                     # A4, A9

        fill_kw = journal_amzn.record_fill.call_args.kwargs
        assert fill_kw["fill_price"] == pytest.approx(0.21)   # A5
        assert fill_kw["journal_id"] == 307                   # A5

        assert risk.pending_entries == 0,     "A8: pending must return to 0"
        assert risk.entries_today == 1,       "A8: entries_today must be 1"
        assert ft_amzn.count() == 0,          "A9: no lingering pending order"

        # ── 12:02 ET: AMZN trailing stop exit ────────────────────────────
        from scripts.session_runner import monitor_positions

        pos = MagicMock()
        pos.symbol = "AMZN"
        pos.option_symbol = "AMZN260603P00247500"
        pos.entry_price = 0.21
        pos.quantity = 1
        pos.strategy_id = "orb"
        pos.journal_id = 307
        pos.entry_time = datetime(2026, 6, 3, 11, 39, tzinfo=ET)

        exit_pm = MagicMock()
        exit_pm.open_positions.return_value = [pos]
        exit_pm.update_price = MagicMock()
        exit_pm.should_exit.return_value = "trailing_stop"
        exit_pm.close = MagicMock()

        exit_quote = MagicMock()
        exit_quote.bid = 0.13
        exit_quote.ask = 0.18
        exit_quote.mid = 0.155

        exit_order_result = MagicMock()
        exit_order_result.order_id = "exit-amzn-001"

        exit_broker = MagicMock()
        exit_broker.get_option_quote = AsyncMock(return_value=exit_quote)
        exit_broker.place_option_order = AsyncMock(return_value=exit_order_result)

        exit_risk = MagicMock()
        exit_risk.record_exit = MagicMock()

        exit_journal = MagicMock()
        exit_journal.record_exit = AsyncMock()
        exit_journal.log_event = AsyncMock()
        exit_journal.commit = AsyncMock()

        exit_settings = MagicMock()
        exit_settings.risk.max_spread_pct = 0.50

        await monitor_positions(
            broker=exit_broker, pm=exit_pm, journal=exit_journal, risk=exit_risk,
            now=datetime(2026, 6, 3, 12, 2, tzinfo=ET),
            dry_run=False, settings=exit_settings,
        )

        exit_kw = exit_journal.record_exit.call_args.kwargs
        assert exit_kw["exit_price"] == pytest.approx(0.13),   "A6: bid price"
        assert exit_kw["realized_pnl"] == pytest.approx(-8.0), "A6: bid-based P&L"

        # ── Session end: ledger update ────────────────────────────────────
        from app.evaluation.ledger import EvaluationLedger
        from app.evaluation.daily_report import DailyReport, StrategyStats

        report = DailyReport(
            date="2026-06-03",
            session_start="10:30",
            session_end="12:30",
            trades_submitted=2,
            trades_filled=1,
            trades_cancelled=2,
            trades_rejected=0,
            realized_pnl=-8.0,
            unrealized_pnl=0.0,
            max_drawdown=8.0,
            slippage_total=0.0,
            spread_cost_estimate=0.0,
            api_errors=0,
            kill_switch_events=0,
            by_strategy=[
                StrategyStats(strategy_id="orb", wins=0, losses=1, realized_pnl=-8.0),
            ],
        )

        ledger = EvaluationLedger(ledger_file="/tmp/test_replay_e2e_ledger.json")
        entry = ledger.add_session(report)

        assert entry.total_trades == 1,          "A7: AMZN trade in ledger"
        assert entry.by_strategy["orb"]["trades"] == 1  # A7
        assert entry.realized_pnl == pytest.approx(-8.0)   # A7: bid-based

        # ── A10: COIN/NVDA capacity check ─────────────────────────────────
        # After AMZN fill (entries=1) and SMH cancel (pending freed):
        # capacity = 1 + 0 = 1 < 3 → COIN and NVDA can still be placed
        coin_capacity = risk.entries_today + risk.pending_entries
        assert coin_capacity == 1, (
            f"A10: COIN capacity={coin_capacity}; must be 1, not 3."
        )
        assert coin_capacity < 3, "A10: COIN/NVDA must not be blocked"
