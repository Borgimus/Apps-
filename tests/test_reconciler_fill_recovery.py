"""
Regression tests for reconciler-recovered fill journaling (Pattern A fix).

Scenario tested end-to-end:
  1. Entry order placed → journal row created (status='open'), fill_tracker registered.
  2. FillTracker stale-cancel fires → cancel() raises 422 → re-check returns CANCELLED
     (edge case: broker race) → _handle_dead("stale_cancelled") called.
     Journal row → status='cancelled'. risk.record_entry_cancelled() called.
  3. Periodic reconciler runs → finds broker position not in local PM.
  4. Reconciler looks up the cancelled journal row by option_symbol + session_date.
  5. Reconciler calls journal.restore_reconciler_fill() → status='open', fill_price set.
  6. Reconciler calls risk.record_entry_filled() → entries_today restored.
  7. Reconciler calls pm.open(journal_id=<original_id>) → position linked to journal.
  8. Normal exit fires → journal.record_exit() called → status='closed', realized_pnl set,
     MFE/MAE written.
  9. health report / evaluation: trade_journal row closed, realized_pnl non-null.
 10. max_symbols_traded_per_day bypass prevented: after reconciler restores,
     risk._entries_today reflects the fill → subsequent order blocked correctly.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ET = ZoneInfo("America/New_York")

SESSION_DATE = "2026-06-12"


# ── DB / Journal fixtures ─────────────────────────────────────────────────────

async def _make_db():
    from app.api.models import Base
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


def _make_pm_settings():
    s = MagicMock()
    s.stop_loss_pct = 0.50
    s.take_profit_pct = 1.00
    s.trailing_stop_pct = 0.25
    s.max_hold_minutes = 120
    s.eod_exit_time = "15:45"
    s.cooldown_after_loss_minutes = 15
    settings = MagicMock()
    settings.position = s
    return settings


def _journal_row(option_symbol: str, strategy_id: str = "vwap_reclaim", status: str = "cancelled"):
    from app.api.models import DBTradeJournal
    return DBTradeJournal(
        session_date=SESSION_DATE,
        strategy_id=strategy_id,
        signal_direction="LONG",
        underlying_symbol="DIA",
        underlying_price=510.0,
        option_symbol=option_symbol,
        expiration=SESSION_DATE,
        strike=510.0,
        option_type="call",
        delta=0.30,
        iv=0.15,
        bid=3.20,
        ask=3.40,
        spread_pct=0.06,
        limit_price=3.30,
        limit_price_mode="marketable_limit",
        quantity=1,
        filled_quantity=0,
        order_id="ORD-DIA-001",
        status=status,
        exit_reason="stale_cancelled" if status == "cancelled" else None,
        is_paper=True,
        entry_time=datetime(2026, 6, 12, 10, 40, tzinfo=ET),
        weekday=4,
    )


def _make_broker_position(option_symbol: str, symbol: str = "DIA", avg_cost: float = 3.25, qty: int = 1):
    bp = MagicMock()
    bp.option_symbol = option_symbol
    bp.symbol = symbol
    bp.is_option = True
    bp.avg_cost = avg_cost
    bp.quantity = qty
    return bp


def _make_broker_no_orders():
    broker = MagicMock()
    broker.get_orders = AsyncMock(return_value=[])
    return broker


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestReconcilerFindsCancelledRow:
    """find_cancelled_for_reconciliation correctly queries by option_symbol + session_date."""

    @pytest.mark.asyncio
    async def test_finds_most_recent_cancelled_row(self):
        from app.trading.trade_journal import TradeJournal
        factory, engine = await _make_db()
        opt = "DIA260612C00510000"
        async with factory() as session:
            row = _journal_row(opt)
            session.add(row)
            await session.commit()

            journal = TradeJournal(session)
            found = await journal.find_cancelled_for_reconciliation(
                option_symbol=opt,
                session_date=SESSION_DATE,
            )
            assert found is not None
            assert found.option_symbol == opt
            assert found.strategy_id == "vwap_reclaim"
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_returns_none_when_no_cancelled_row(self):
        from app.trading.trade_journal import TradeJournal
        factory, engine = await _make_db()
        async with factory() as session:
            journal = TradeJournal(session)
            found = await journal.find_cancelled_for_reconciliation(
                option_symbol="NONEXISTENT260612C00000000",
                session_date=SESSION_DATE,
            )
            assert found is None
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_ignores_closed_rows(self):
        from app.trading.trade_journal import TradeJournal
        factory, engine = await _make_db()
        opt = "DIA260612C00510001"
        async with factory() as session:
            row = _journal_row(opt, status="closed")
            session.add(row)
            await session.commit()

            journal = TradeJournal(session)
            found = await journal.find_cancelled_for_reconciliation(
                option_symbol=opt,
                session_date=SESSION_DATE,
            )
            assert found is None
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_ignores_different_session_date(self):
        from app.trading.trade_journal import TradeJournal
        factory, engine = await _make_db()
        opt = "DIA260612C00510002"
        async with factory() as session:
            row = _journal_row(opt)
            session.add(row)
            await session.commit()

            journal = TradeJournal(session)
            found = await journal.find_cancelled_for_reconciliation(
                option_symbol=opt,
                session_date="2026-06-11",  # different date
            )
            assert found is None
        await engine.dispose()


class TestRestoreReconcilerFill:
    """restore_reconciler_fill updates the cancelled row to reflect the actual fill."""

    @pytest.mark.asyncio
    async def test_restores_fill_price_and_status(self):
        from app.trading.trade_journal import TradeJournal
        factory, engine = await _make_db()
        opt = "DIA260612C00510003"
        async with factory() as session:
            row = _journal_row(opt)
            session.add(row)
            await session.flush()
            row_id = row.id

            journal = TradeJournal(session)
            filled_at = datetime(2026, 6, 12, 10, 50, tzinfo=ET)
            await journal.restore_reconciler_fill(
                journal_id=row_id,
                fill_price=3.25,
                filled_quantity=1,
                filled_at=filled_at,
            )
            await session.commit()

            refreshed = await session.get(type(row), row_id)
            assert refreshed.fill_price == pytest.approx(3.25)
            assert refreshed.filled_quantity == 1
            assert refreshed.status == "open"
            assert refreshed.exit_reason is None
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_sets_slippage_from_limit_price(self):
        from app.trading.trade_journal import TradeJournal
        factory, engine = await _make_db()
        opt = "DIA260612C00510004"
        async with factory() as session:
            row = _journal_row(opt)
            session.add(row)
            await session.flush()
            row_id = row.id

            journal = TradeJournal(session)
            await journal.restore_reconciler_fill(
                journal_id=row_id,
                fill_price=3.25,
                filled_quantity=1,
                filled_at=datetime(2026, 6, 12, 10, 50, tzinfo=ET),
            )
            await session.commit()

            refreshed = await session.get(type(row), row_id)
            # slippage = fill_price(3.25) - limit_price(3.30) = -0.05
            assert refreshed.slippage == pytest.approx(-0.05, abs=0.001)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_sets_time_to_fill(self):
        from app.trading.trade_journal import TradeJournal
        factory, engine = await _make_db()
        opt = "DIA260612C00510005"
        async with factory() as session:
            row = _journal_row(opt)
            session.add(row)
            await session.flush()
            row_id = row.id

            journal = TradeJournal(session)
            # entry_time = 10:40, filled_at = 10:50 → 600 seconds
            filled_at = datetime(2026, 6, 12, 10, 50, tzinfo=ET)
            await journal.restore_reconciler_fill(
                journal_id=row_id,
                fill_price=3.25,
                filled_quantity=1,
                filled_at=filled_at,
            )
            await session.commit()

            refreshed = await session.get(type(row), row_id)
            assert refreshed.time_to_fill_secs == pytest.approx(600.0, abs=5.0)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_noop_for_missing_row(self):
        from app.trading.trade_journal import TradeJournal
        factory, engine = await _make_db()
        async with factory() as session:
            journal = TradeJournal(session)
            # Should not raise
            await journal.restore_reconciler_fill(
                journal_id=99999,
                fill_price=3.25,
                filled_quantity=1,
                filled_at=datetime.now(tz=ET),
            )
        await engine.dispose()


class TestReconcilerRestoresJournalRow:
    """
    Reconciler.reconcile() with journal/risk/session_date: finds cancelled row,
    restores it, calls risk.record_entry_filled(), passes journal_id to pm.open().
    """

    @pytest.mark.asyncio
    async def test_reconciler_restores_cancelled_row_and_links_position(self):
        from app.trading.reconciler import Reconciler
        from app.trading.position_manager import PositionManager
        from app.trading.trade_journal import TradeJournal

        factory, engine = await _make_db()
        opt = "DIA260612C00510006"
        async with factory() as session:
            row = _journal_row(opt)
            session.add(row)
            await session.commit()
            row_id = row.id

            journal = TradeJournal(session)
            pm = PositionManager(_make_pm_settings())

            risk = MagicMock()
            risk.record_entry_filled = MagicMock()

            broker = _make_broker_no_orders()
            bp = _make_broker_position(opt, avg_cost=3.25, qty=1)
            broker.get_positions = AsyncMock(return_value=[bp])
            broker.get_orders = AsyncMock(return_value=[])

            ft = MagicMock()
            ft.count = MagicMock(return_value=0)
            ft._pending = {}

            now = datetime(2026, 6, 12, 10, 50, tzinfo=ET)
            reconciler = Reconciler()
            result = await reconciler.reconcile(
                broker, pm, ft, now,
                journal=journal,
                risk=risk,
                session_date=SESSION_DATE,
            )

            # One position repaired
            assert len(result.repaired) == 1

            # PM has the position with the original strategy_id (not "reconciled")
            assert pm.has_position(opt)
            pos = pm._positions[opt]
            assert pos.strategy_id == "vwap_reclaim"
            assert pos.journal_id == row_id

            # risk.record_entry_filled called
            risk.record_entry_filled.assert_called_once()

            # Journal row is now open with fill price
            await session.refresh(row)
            assert row.status == "open"
            assert row.fill_price == pytest.approx(3.25)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_reconciler_falls_back_to_reconciled_when_no_journal_row(self):
        from app.trading.reconciler import Reconciler
        from app.trading.position_manager import PositionManager
        from app.trading.trade_journal import TradeJournal

        factory, engine = await _make_db()
        opt = "XLF260612C00053500"
        async with factory() as session:
            # No journal row for this symbol
            journal = TradeJournal(session)
            pm = PositionManager(_make_pm_settings())

            risk = MagicMock()
            risk.record_entry_filled = MagicMock()

            broker = _make_broker_no_orders()
            bp = _make_broker_position(opt, symbol="XLF", avg_cost=0.11, qty=1)
            broker.get_positions = AsyncMock(return_value=[bp])
            broker.get_orders = AsyncMock(return_value=[])

            ft = MagicMock()
            ft.count = MagicMock(return_value=0)
            ft._pending = {}

            now = datetime(2026, 6, 12, 11, 30, tzinfo=ET)
            reconciler = Reconciler()
            await reconciler.reconcile(
                broker, pm, ft, now,
                journal=journal,
                risk=risk,
                session_date=SESSION_DATE,
            )

            # Position opened with fallback strategy_id
            assert pm.has_position(opt)
            pos = pm._positions[opt]
            assert pos.strategy_id == "reconciled"
            assert pos.journal_id is None

            # risk.record_entry_filled still called (position was filled)
            risk.record_entry_filled.assert_called_once()
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_reconciler_without_journal_skips_restore(self):
        """Calling reconcile() without journal/risk still opens position (backward compat)."""
        from app.trading.reconciler import Reconciler
        from app.trading.position_manager import PositionManager

        factory, engine = await _make_db()
        opt = "IWM260612C00296000"
        pm = PositionManager(_make_pm_settings())

        broker = _make_broker_no_orders()
        bp = _make_broker_position(opt, symbol="IWM", avg_cost=0.17, qty=1)
        broker.get_positions = AsyncMock(return_value=[bp])
        broker.get_orders = AsyncMock(return_value=[])

        ft = MagicMock()
        ft.count = MagicMock(return_value=0)
        ft._pending = {}

        reconciler = Reconciler()
        result = await reconciler.reconcile(broker, pm, ft, datetime.now(tz=ET))

        assert pm.has_position(opt)
        assert len(result.repaired) == 1
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_reconciler_preserves_original_strategy_id(self):
        """Critically: strategy_id must be from the original journal row, not 'reconciled'."""
        from app.trading.reconciler import Reconciler
        from app.trading.position_manager import PositionManager
        from app.trading.trade_journal import TradeJournal

        factory, engine = await _make_db()
        opt = "IWM260612C00296001"
        async with factory() as session:
            row = _journal_row(opt, strategy_id="orb")
            session.add(row)
            await session.commit()

            journal = TradeJournal(session)
            pm = PositionManager(_make_pm_settings())
            risk = MagicMock()
            risk.record_entry_filled = MagicMock()

            broker = _make_broker_no_orders()
            bp = _make_broker_position(opt, symbol="IWM", avg_cost=0.17)
            broker.get_positions = AsyncMock(return_value=[bp])
            broker.get_orders = AsyncMock(return_value=[])

            ft = MagicMock()
            ft.count = MagicMock(return_value=0)
            ft._pending = {}

            reconciler = Reconciler()
            await reconciler.reconcile(
                broker, pm, ft, datetime.now(tz=ET),
                journal=journal, risk=risk, session_date=SESSION_DATE,
            )

            pos = pm._positions[opt]
            assert pos.strategy_id == "orb"
        await engine.dispose()


class TestRiskCounterRestoredByReconciler:
    """
    After reconciler recovery, risk._entries_today reflects the fill
    so subsequent orders are blocked by max_trades_per_day if limit reached.
    """

    @pytest.mark.asyncio
    async def test_entries_today_incremented_on_recovery(self):
        from app.trading.reconciler import Reconciler
        from app.trading.position_manager import PositionManager
        from app.trading.trade_journal import TradeJournal
        from app.risk.risk_manager import RiskManager

        factory, engine = await _make_db()
        opt = "TSLA260612P00380000"
        async with factory() as session:
            row = _journal_row(opt, strategy_id="vwap_reclaim")
            session.add(row)
            await session.commit()

            journal = TradeJournal(session)
            pm = PositionManager(_make_pm_settings())

            settings = MagicMock()
            settings.risk = MagicMock()
            settings.risk.max_trades_per_day = 3
            settings.risk.max_daily_loss = 0.10
            settings.risk.max_risk_per_trade = 0.02
            settings.risk.allow_earnings_trades = True
            settings.market_open = "09:30"
            settings.market_close = "16:00"
            settings.no_trade_open_buffer_minutes = 15
            settings.no_trade_close_buffer_minutes = 15
            settings.position.eod_exit_time = "15:45"
            settings.position.min_entry_minutes_before_eod = 30
            settings.is_kill_switch_active.return_value = False
            settings.live_trading_enabled = False
            risk = RiskManager(settings)
            from decimal import Decimal
            risk.start_session(Decimal("100000"))
            # Simulate two prior filled entries
            risk.record_entry_pending()
            risk.record_entry_filled()
            risk.record_entry_pending()
            risk.record_entry_filled()
            # entries_today = 2, pending = 0

            broker = _make_broker_no_orders()
            bp = _make_broker_position(opt, symbol="TSLA", avg_cost=1.40)
            broker.get_positions = AsyncMock(return_value=[bp])
            broker.get_orders = AsyncMock(return_value=[])

            ft = MagicMock()
            ft.count = MagicMock(return_value=0)
            ft._pending = {}

            reconciler = Reconciler()
            await reconciler.reconcile(
                broker, pm, ft, datetime.now(tz=ET),
                journal=journal, risk=risk, session_date=SESSION_DATE,
            )

            # entries_today should now be 3 (2 prior + 1 reconciled)
            assert risk.entries_today == 3

            # A new order check should be blocked (capacity_used = 3 >= max=3)
            from app.brokers.broker_interface import OrderRequest, OrderSide, OrderType
            req = OrderRequest(
                symbol="IWM",
                option_symbol="IWM260612C00296000",
                side=OrderSide.BUY_TO_OPEN,
                quantity=1,
                order_type=OrderType.LIMIT,
                limit_price=0.19,
                strategy_id="orb",
            )
            from decimal import Decimal
            result = risk.check_order(req, Decimal("100000"), now=datetime.now(tz=ET))
            assert not result.passed
        await engine.dispose()


class TestMFEMAEPersisted:
    """peak_price, trough_price, mfe, mae are written to trade_journal on exit."""

    @pytest.mark.asyncio
    async def test_record_exit_stores_mfe_mae(self):
        from app.api.models import DBTradeJournal
        from app.trading.trade_journal import TradeJournal

        factory, engine = await _make_db()
        async with factory() as session:
            row = DBTradeJournal(
                session_date=SESSION_DATE,
                strategy_id="vwap_reclaim",
                underlying_symbol="DIA",
                option_symbol="DIA260612C00510007",
                status="open",
                fill_price=3.25,
                limit_price=3.30,
                quantity=1,
                entry_time=datetime(2026, 6, 12, 10, 50, tzinfo=ET),
                is_paper=True,
            )
            session.add(row)
            await session.flush()
            row_id = row.id

            journal = TradeJournal(session)
            exit_time = datetime(2026, 6, 12, 11, 51, tzinfo=ET)
            await journal.record_exit(
                journal_id=row_id,
                exit_time=exit_time,
                exit_price=3.11,
                exit_reason="trailing_stop",
                realized_pnl=-14.0,
                hold_duration_secs=3660.0,
                peak_price=3.50,
                trough_price=3.10,
                mfe=25.0,
                mae=-15.0,
            )
            await session.commit()

            refreshed = await session.get(DBTradeJournal, row_id)
            assert refreshed.status == "closed"
            assert refreshed.peak_price == pytest.approx(3.50)
            assert refreshed.trough_price == pytest.approx(3.10)
            assert refreshed.mfe == pytest.approx(25.0)
            assert refreshed.mae == pytest.approx(-15.0)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_record_exit_without_mfe_leaves_columns_none(self):
        from app.api.models import DBTradeJournal
        from app.trading.trade_journal import TradeJournal

        factory, engine = await _make_db()
        async with factory() as session:
            row = DBTradeJournal(
                session_date=SESSION_DATE,
                strategy_id="orb",
                underlying_symbol="IWM",
                option_symbol="IWM260612C00296002",
                status="open",
                fill_price=0.17,
                limit_price=0.19,
                quantity=1,
                entry_time=datetime(2026, 6, 12, 12, 21, tzinfo=ET),
                is_paper=True,
            )
            session.add(row)
            await session.flush()
            row_id = row.id

            journal = TradeJournal(session)
            await journal.record_exit(
                journal_id=row_id,
                exit_time=datetime(2026, 6, 12, 12, 31, tzinfo=ET),
                exit_price=0.21,
                exit_reason="eod_exit",
                realized_pnl=4.0,
                hold_duration_secs=600.0,
            )
            await session.commit()

            refreshed = await session.get(DBTradeJournal, row_id)
            assert refreshed.status == "closed"
            assert refreshed.peak_price is None
            assert refreshed.mfe is None
            assert refreshed.mae is None
        await engine.dispose()


class TestFullPatternAEndToEnd:
    """
    Full Pattern A scenario with real DB:
    stale-cancel → reconciler finds broker position → journal row restored →
    strategy_id preserved → exit journaled → realized_pnl correct.
    """

    @pytest.mark.asyncio
    async def test_full_pattern_a_trade_appears_closed_in_db(self):
        from app.api.models import DBTradeJournal
        from app.trading.reconciler import Reconciler
        from app.trading.trade_journal import TradeJournal
        from app.trading.position_manager import PositionManager

        factory, engine = await _make_db()
        opt = "DIA260612C00510008"
        async with factory() as session:
            # 1. Entry order placed — journal row created with status='open'
            #    FillTracker stale-cancel sets it to 'cancelled'
            row = _journal_row(opt, strategy_id="vwap_reclaim", status="cancelled")
            session.add(row)
            await session.commit()
            row_id = row.id

            journal = TradeJournal(session)
            pm = PositionManager(_make_pm_settings())
            risk = MagicMock()
            risk.record_entry_filled = MagicMock()

            broker = _make_broker_no_orders()
            bp = _make_broker_position(opt, avg_cost=3.25)
            broker.get_positions = AsyncMock(return_value=[bp])
            broker.get_orders = AsyncMock(return_value=[])

            ft = MagicMock()
            ft.count = MagicMock(return_value=0)
            ft._pending = {}

            # 2. Reconciler runs — discovers broker position, restores journal row
            now = datetime(2026, 6, 12, 10, 50, tzinfo=ET)
            reconciler = Reconciler()
            await reconciler.reconcile(
                broker, pm, ft, now,
                journal=journal, risk=risk, session_date=SESSION_DATE,
            )

            pos = pm._positions[opt]
            assert pos.journal_id == row_id
            assert pos.strategy_id == "vwap_reclaim"
            assert pos.entry_price == pytest.approx(3.25)

            # 3. Position price moves — update_price sets peak/trough
            pm.update_price(opt, 3.40)  # price rises (MFE)
            pm.update_price(opt, 3.11)  # price falls (exit trigger)

            # 4. Exit fires — journal.record_exit called
            exit_time = datetime(2026, 6, 12, 11, 51, tzinfo=ET)
            hold_secs = (exit_time - now).total_seconds()
            pnl = (3.11 - 3.25) * 100 * 1  # = -14.0
            mfe = (pos.peak_price - pos.entry_price) * 100 * 1  # = (3.40-3.25)*100 = 15.0
            mae = (pos.trough_price - pos.entry_price) * 100 * 1  # = (3.11-3.25)*100 = -14.0

            await journal.record_exit(
                journal_id=pos.journal_id,
                exit_time=exit_time,
                exit_price=3.11,
                exit_reason="trailing_stop",
                realized_pnl=pnl,
                hold_duration_secs=hold_secs,
                peak_price=pos.peak_price,
                trough_price=pos.trough_price,
                mfe=mfe,
                mae=mae,
            )
            await session.commit()

            # 5. Verify DB row is complete and correct
            refreshed = await session.get(DBTradeJournal, row_id)
            assert refreshed.status == "closed"
            assert refreshed.fill_price == pytest.approx(3.25)
            assert refreshed.exit_price == pytest.approx(3.11)
            assert refreshed.realized_pnl == pytest.approx(-14.0)
            assert refreshed.strategy_id == "vwap_reclaim"
            assert refreshed.peak_price == pytest.approx(3.40)
            assert refreshed.mfe == pytest.approx(15.0)
            assert refreshed.mae == pytest.approx(-14.0)
        await engine.dispose()
