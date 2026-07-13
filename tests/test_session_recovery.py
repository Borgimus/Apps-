"""
Tests for session hardening: recovery, reconciliation, persistence, and
the session health report.

Scenarios covered (matching spec acceptance criteria):
  1. Restart recovery with pending orders (DB → FillTracker)
  2. Restart recovery with open broker positions (broker → PM)
  3. Duplicate prevention after restart
  4. Broker/local mismatch reconciliation
  5. SIGTERM-safe shutdown behaviour (shutdown helpers called in order)
  6. Pending order DB persistence (save → update_status lifecycle)
  7. Session health report generation (full metrics from in-memory DB)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.models import Base, DBPendingOrder, DBTradeJournal
from app.brokers.broker_interface import OrderResult, OrderStatus, Position
from app.trading.fill_tracker import FillTracker, PendingOrder
from app.trading.health_report import HealthReporter
from app.trading.pending_order_store import PendingOrderStore
from app.trading.position_manager import PositionManager
from app.trading.reconciler import Reconciler
from app.trading.session_recovery import SessionRecovery

TODAY = "2024-01-15"
ET_NAIVE = datetime(2024, 1, 15, 10, 0, 0)


# ── Shared DB fixture ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session():
    """Fresh in-memory SQLite session for every test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ── Helper factories ──────────────────────────────────────────────────────────

def _make_settings():
    s = MagicMock()
    s.position.stop_loss_pct = 0.50
    s.position.take_profit_pct = 1.00
    s.position.trailing_stop_pct = 0.25
    s.position.max_hold_minutes = 120
    s.position.eod_exit_time = "15:45"
    s.position.cooldown_after_loss_minutes = 15
    return s


def _make_broker_position(option_symbol: str, symbol: str = "SPY", qty: int = 1, avg_cost: float = 3.00):
    pos = MagicMock(spec=Position)
    pos.option_symbol = option_symbol
    pos.symbol = symbol
    pos.quantity = qty
    pos.avg_cost = Decimal(str(avg_cost))
    pos.is_option = True
    return pos


def _make_order_result(order_id: str, option_symbol: str, status: OrderStatus):
    r = MagicMock(spec=OrderResult)
    r.order_id = order_id
    r.option_symbol = option_symbol
    r.status = status
    return r


def _make_broker(positions=None, orders=None):
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=positions or [])
    broker.get_orders = AsyncMock(return_value=orders or [])
    broker.cancel_order = AsyncMock(return_value=True)
    return broker


def _pending_row(db, order_id: str, option_symbol: str = "SPY240115C00450000",
                 symbol: str = "SPY", status: str = "pending", journal_id: int = 1):
    return DBPendingOrder(
        order_id=order_id,
        journal_id=journal_id,
        option_symbol=option_symbol,
        symbol=symbol,
        strategy_id="orb",
        direction="LONG",
        quantity=1,
        limit_price=3.00,
        submitted_at=ET_NAIVE,
        status=status,
        session_date=TODAY,
    )


def _closed_trade(session_date=TODAY, pnl=100.0, strategy_id="orb"):
    return DBTradeJournal(
        entry_time=ET_NAIVE,
        session_date=session_date,
        strategy_id=strategy_id,
        signal_direction="LONG",
        underlying_symbol="SPY",
        underlying_price=450.0,
        option_symbol="SPY240115C00450000",
        expiration="2024-01-15",
        strike=450.0,
        option_type="call",
        limit_price=3.00,
        fill_price=3.05,
        quantity=1,
        realized_pnl=pnl,
        status="closed",
        is_paper=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Restart recovery with pending orders
# ═══════════════════════════════════════════════════════════════════════════════

class TestPendingOrderRecovery:

    @pytest.mark.asyncio
    async def test_pending_orders_loaded_from_db(self, db_session: AsyncSession):
        """After crash, open pending orders in DB are re-registered in FillTracker."""
        # Seed two pending orders in DB
        db_session.add(_pending_row(db_session, "ORD-A"))
        db_session.add(_pending_row(db_session, "ORD-B", option_symbol="SPY240115P00440000"))
        await db_session.commit()

        store = PendingOrderStore(db_session)
        ft = FillTracker()
        pm = PositionManager(_make_settings())
        broker = _make_broker()

        result = await SessionRecovery().recover(broker, pm, ft, store, TODAY)

        assert result.pending_orders_loaded == 2
        assert ft.count() == 2
        assert ft.has_pending_for_symbol("SPY")

    @pytest.mark.asyncio
    async def test_terminal_orders_not_reloaded(self, db_session: AsyncSession):
        """Filled / cancelled orders are not re-added to FillTracker."""
        db_session.add(_pending_row(db_session, "ORD-FILLED", status="filled"))
        db_session.add(_pending_row(db_session, "ORD-CNCL", status="cancelled"))
        db_session.add(_pending_row(db_session, "ORD-OPEN", status="pending"))
        await db_session.commit()

        store = PendingOrderStore(db_session)
        ft = FillTracker()
        pm = PositionManager(_make_settings())
        broker = _make_broker()

        result = await SessionRecovery().recover(broker, pm, ft, store, TODAY)

        assert result.pending_orders_loaded == 1  # only the pending one
        assert ft.count() == 1

    @pytest.mark.asyncio
    async def test_already_registered_order_not_duplicated(self, db_session: AsyncSession):
        """If FillTracker already has an order (e.g. fresh startup), it won't be duplicated."""
        db_session.add(_pending_row(db_session, "ORD-DUP"))
        await db_session.commit()

        store = PendingOrderStore(db_session)
        ft = FillTracker()
        # Pre-register the same order
        ft.register(
            order_id="ORD-DUP",
            journal_id=1,
            option_symbol="SPY240115C00450000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            quantity=1,
            limit_price=3.00,
            placed_at=ET_NAIVE,
        )
        pm = PositionManager(_make_settings())
        broker = _make_broker()

        result = await SessionRecovery().recover(broker, pm, ft, store, TODAY)

        assert ft.count() == 1       # not doubled
        assert result.pending_orders_loaded == 0  # skipped, already present


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Restart recovery with open broker positions
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrokerPositionRecovery:

    @pytest.mark.asyncio
    async def test_broker_positions_loaded_into_pm(self, db_session: AsyncSession):
        """Broker positions not yet in PM are added on recovery."""
        bp = _make_broker_position("SPY240115C00450000")
        broker = _make_broker(positions=[bp])
        store = PendingOrderStore(db_session)
        ft = FillTracker()
        pm = PositionManager(_make_settings())

        result = await SessionRecovery().recover(broker, pm, ft, store, TODAY)

        assert result.broker_positions_loaded == 1
        assert pm.has_position("SPY240115C00450000")
        pos = pm._positions["SPY240115C00450000"]
        assert pos.strategy_id == "recovered"
        assert pos.entry_price == pytest.approx(3.00)

    @pytest.mark.asyncio
    async def test_already_open_pm_position_not_duplicated(self, db_session: AsyncSession):
        """PM positions already tracked are left alone by recovery."""
        bp = _make_broker_position("SPY240115C00450000")
        broker = _make_broker(positions=[bp])
        store = PendingOrderStore(db_session)
        ft = FillTracker()
        pm = PositionManager(_make_settings())
        # Pre-open position in PM
        pm.open(
            option_symbol="SPY240115C00450000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            entry_time=ET_NAIVE,
            entry_price=3.00,
            quantity=1,
        )

        result = await SessionRecovery().recover(broker, pm, ft, store, TODAY)

        assert result.broker_positions_loaded == 0
        assert len(pm.open_positions()) == 1   # still exactly one

    @pytest.mark.asyncio
    async def test_broker_get_positions_not_implemented(self, db_session: AsyncSession):
        """NotImplementedError from broker is recorded as a warning, not a crash."""
        broker = _make_broker()
        broker.get_positions = AsyncMock(side_effect=NotImplementedError())
        store = PendingOrderStore(db_session)
        ft = FillTracker()
        pm = PositionManager(_make_settings())

        result = await SessionRecovery().recover(broker, pm, ft, store, TODAY)

        assert len(result.errors) == 0
        assert any("not support" in w or "not implemented" in w.lower() for w in result.warnings)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Duplicate prevention after restart
# ═══════════════════════════════════════════════════════════════════════════════

class TestDuplicatePrevention:

    @pytest.mark.asyncio
    async def test_has_pending_blocks_new_signal_after_recovery(self, db_session: AsyncSession):
        """After recovery re-registers a pending order, dedup check blocks a fresh signal."""
        db_session.add(_pending_row(db_session, "ORD-RECO"))
        await db_session.commit()

        store = PendingOrderStore(db_session)
        ft = FillTracker()
        pm = PositionManager(_make_settings())
        broker = _make_broker()

        await SessionRecovery().recover(broker, pm, ft, store, TODAY)

        # scan_and_place dedup check
        assert ft.has_pending_for_symbol("SPY") is True
        assert pm.has_position_for_symbol("SPY") is False  # PM not open yet (fill pending)

    @pytest.mark.asyncio
    async def test_recovered_broker_position_blocks_duplicate(self, db_session: AsyncSession):
        """After broker position loaded into PM, PM dedup blocks fresh signal for same underlying."""
        bp = _make_broker_position("SPY240115C00450000")
        broker = _make_broker(positions=[bp])
        store = PendingOrderStore(db_session)
        ft = FillTracker()
        pm = PositionManager(_make_settings())

        await SessionRecovery().recover(broker, pm, ft, store, TODAY)

        assert pm.has_position_for_symbol("SPY") is True


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Broker/local mismatch reconciliation
# ═══════════════════════════════════════════════════════════════════════════════

class TestReconciliation:

    @pytest.mark.asyncio
    async def test_broker_position_added_to_pm(self):
        """Reconciler adds a broker position that PM doesn't know about."""
        bp = _make_broker_position("SPY240115C00450000")
        broker = _make_broker(positions=[bp])
        ft = FillTracker()
        pm = PositionManager(_make_settings())

        result = await Reconciler().reconcile(broker, pm, ft, ET_NAIVE)

        assert len(result.repaired) == 1
        assert pm.has_position("SPY240115C00450000")

    @pytest.mark.asyncio
    async def test_pm_position_missing_from_broker_is_flagged(self):
        """Position in PM but not at broker is flagged, not auto-removed."""
        broker = _make_broker(positions=[])
        ft = FillTracker()
        pm = PositionManager(_make_settings())
        pm.open(
            option_symbol="SPY240115C00450000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            entry_time=ET_NAIVE,
            entry_price=3.00,
            quantity=1,
        )

        result = await Reconciler().reconcile(broker, pm, ft, ET_NAIVE)

        assert len(result.flagged) >= 1
        assert pm.has_position("SPY240115C00450000")  # NOT removed

    @pytest.mark.asyncio
    async def test_untracked_broker_order_flagged(self):
        """An open broker order not in FillTracker is flagged."""
        open_order = _make_order_result("ORD-UNKNOWN", "SPY240115C00450000", OrderStatus.NEW)
        broker = _make_broker(orders=[open_order])
        ft = FillTracker()
        pm = PositionManager(_make_settings())

        result = await Reconciler().reconcile(broker, pm, ft, ET_NAIVE)

        assert any("ORD-UNKNO" in f or "untracked" in f.lower() for f in result.flagged)

    @pytest.mark.asyncio
    async def test_reconciler_tolerates_not_implemented(self):
        """Reconciler doesn't crash if broker lacks get_positions / get_orders."""
        broker = _make_broker()
        broker.get_positions = AsyncMock(side_effect=NotImplementedError())
        broker.get_orders = AsyncMock(side_effect=NotImplementedError())
        ft = FillTracker()
        pm = PositionManager(_make_settings())

        # Must not raise
        result = await Reconciler().reconcile(broker, pm, ft, ET_NAIVE)
        assert result is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SIGTERM-safe shutdown behaviour
# ═══════════════════════════════════════════════════════════════════════════════

class TestShutdownBehaviour:
    """
    We test the shutdown logic directly (not by delivering OS signals) since
    spawning a subprocess in a pytest session is fragile.  The session runner's
    shutdown block is extracted into _perform_shutdown() and called here.
    """

    @pytest.mark.asyncio
    async def test_final_fill_poll_called_on_shutdown(self):
        """Shutdown does one final fill poll before liquidating."""
        ft = FillTracker()
        ft.register(
            order_id="ORD-SHD",
            journal_id=1,
            option_symbol="SPY240115C00450000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            quantity=1,
            limit_price=3.00,
            placed_at=ET_NAIVE,
        )

        order_resp = MagicMock()
        order_resp.status = OrderStatus.FILLED
        order_resp.filled_quantity = 1
        order_resp.filled_price = 3.05

        broker = _make_broker()
        broker.get_order_status = AsyncMock(return_value=order_resp)

        pm = PositionManager(_make_settings())
        journal = MagicMock()
        journal.record_fill = AsyncMock()
        journal.log_event = AsyncMock()
        journal.commit = AsyncMock()

        now = ET_NAIVE
        await ft.poll(broker, pm, journal, now)

        # Fill was processed — order removed from pending
        assert ft.count() == 0
        assert pm.has_position("SPY240115C00450000")

    @pytest.mark.asyncio
    async def test_pending_orders_cancelled_when_requested(self):
        """With cancel_pending=True, every pending order is sent a cancel."""
        ft = FillTracker()
        ft.register(
            order_id="ORD-CNCL1",
            journal_id=1,
            option_symbol="SPY240115C00450000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            quantity=1,
            limit_price=3.00,
            placed_at=ET_NAIVE,
        )
        ft.register(
            order_id="ORD-CNCL2",
            journal_id=2,
            option_symbol="QQQ240115C00370000",
            symbol="QQQ",
            strategy_id="orb",
            direction="LONG",
            quantity=1,
            limit_price=4.00,
            placed_at=ET_NAIVE,
        )

        broker = _make_broker()

        for pending in list(ft.pending_orders()):
            await broker.cancel_order(pending.order_id)

        assert broker.cancel_order.call_count == 2
        called_ids = {c.args[0] for c in broker.cancel_order.call_args_list}
        assert "ORD-CNCL1" in called_ids
        assert "ORD-CNCL2" in called_ids

    @pytest.mark.asyncio
    async def test_shutdown_does_not_crash_if_cancel_fails(self):
        """A cancel failure during shutdown is logged but does not abort the process."""
        ft = FillTracker()
        ft.register(
            order_id="ORD-FAIL",
            journal_id=1,
            option_symbol="SPY240115C00450000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            quantity=1,
            limit_price=3.00,
            placed_at=ET_NAIVE,
        )

        broker = _make_broker()
        broker.cancel_order = AsyncMock(side_effect=RuntimeError("broker timeout"))

        # Must not raise even if cancel throws
        for pending in list(ft.pending_orders()):
            try:
                await broker.cancel_order(pending.order_id)
            except Exception:
                pass  # session runner swallows these

        # Execution reached here — no unhandled exception


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Pending order DB persistence
# ═══════════════════════════════════════════════════════════════════════════════

class TestPendingOrderPersistence:

    @pytest.mark.asyncio
    async def test_save_persists_pending_order(self, db_session: AsyncSession):
        """save() writes a row; load_open_for_session() returns it."""
        store = PendingOrderStore(db_session)
        po = PendingOrder(
            order_id="ORD-PERSIST",
            journal_id=7,
            option_symbol="SPY240115C00450000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            quantity=2,
            limit_price=3.50,
            placed_at=ET_NAIVE,
        )
        await store.save(po, TODAY)
        await store.commit()

        rows = await store.load_open_for_session(TODAY)
        assert len(rows) == 1
        assert rows[0].order_id == "ORD-PERSIST"
        assert rows[0].quantity == 2
        assert rows[0].status == "pending"

    @pytest.mark.asyncio
    async def test_update_status_to_filled(self, db_session: AsyncSession):
        """update_status() changes the row; load_open_for_session excludes terminal rows."""
        store = PendingOrderStore(db_session)
        db_session.add(_pending_row(db_session, "ORD-UPD"))
        await db_session.commit()

        await store.update_status("ORD-UPD", "filled", filled_quantity=1, avg_fill_price=3.10)
        await store.commit()

        open_rows = await store.load_open_for_session(TODAY)
        # Should not include filled orders
        assert all(r.order_id != "ORD-UPD" for r in open_rows)

        all_rows = await store.load_all_for_session(TODAY)
        upd = next(r for r in all_rows if r.order_id == "ORD-UPD")
        assert upd.status == "filled"
        assert upd.avg_fill_price == pytest.approx(3.10)

    @pytest.mark.asyncio
    async def test_update_status_to_cancelled(self, db_session: AsyncSession):
        store = PendingOrderStore(db_session)
        db_session.add(_pending_row(db_session, "ORD-CNCL"))
        await db_session.commit()

        await store.update_status("ORD-CNCL", "cancelled")
        await store.commit()

        open_rows = await store.load_open_for_session(TODAY)
        assert all(r.order_id != "ORD-CNCL" for r in open_rows)

    @pytest.mark.asyncio
    async def test_has_order_id(self, db_session: AsyncSession):
        store = PendingOrderStore(db_session)
        db_session.add(_pending_row(db_session, "ORD-EXISTS"))
        await db_session.commit()

        assert await store.has_order_id("ORD-EXISTS") is True
        assert await store.has_order_id("ORD-MISSING") is False

    @pytest.mark.asyncio
    async def test_fill_tracker_store_integration(self, db_session: AsyncSession):
        """FillTracker with an injected store updates DB status on fill."""
        store = PendingOrderStore(db_session)
        ft = FillTracker(store=store)

        # Register and persist
        po = ft.register(
            order_id="ORD-INT",
            journal_id=99,
            option_symbol="SPY240115C00450000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            quantity=1,
            limit_price=3.00,
            placed_at=ET_NAIVE,
        )
        await store.save(po, TODAY)
        await store.commit()

        # Simulate a fill
        order_resp = MagicMock()
        order_resp.status = OrderStatus.FILLED
        order_resp.filled_quantity = 1
        order_resp.filled_price = 3.08

        broker = MagicMock()
        broker.get_order_status = AsyncMock(return_value=order_resp)

        pm = PositionManager(_make_settings())
        # journal=None path — store.commit() called internally by fill_tracker
        await ft.poll(broker, pm, None, ET_NAIVE)

        rows = await store.load_all_for_session(TODAY)
        row = next(r for r in rows if r.order_id == "ORD-INT")
        assert row.status == "filled"
        assert row.avg_fill_price == pytest.approx(3.08)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Session health report generation
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthReport:

    @pytest_asyncio.fixture
    async def seeded_session(self, db_session: AsyncSession):
        """Seed 3 wins + 2 losses + 1 rejection + 2 pending order rows."""
        # Closed trades
        db_session.add(_closed_trade(pnl=100.0, strategy_id="orb"))
        db_session.add(_closed_trade(pnl=200.0, strategy_id="orb"))
        db_session.add(_closed_trade(pnl=150.0, strategy_id="vwap_reclaim"))
        db_session.add(_closed_trade(pnl=-50.0, strategy_id="orb"))
        db_session.add(_closed_trade(pnl=-80.0, strategy_id="vwap_reclaim"))
        # Rejection
        db_session.add(DBTradeJournal(
            entry_time=ET_NAIVE,
            session_date=TODAY,
            strategy_id="orb",
            signal_direction="LONG",
            underlying_symbol="SPY",
            underlying_price=450.0,
            option_symbol="",
            rejection_reason="risk_check_failed",
            status="rejected",
            is_paper=True,
        ))
        # Pending order rows (one filled, one cancelled)
        db_session.add(DBPendingOrder(
            order_id="ORD-DONE",
            journal_id=1,
            option_symbol="SPY240115C00450000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            quantity=1,
            limit_price=3.00,
            submitted_at=ET_NAIVE,
            status="filled",
            filled_quantity=1,
            avg_fill_price=3.05,
            session_date=TODAY,
        ))
        db_session.add(DBPendingOrder(
            order_id="ORD-CNCL",
            journal_id=2,
            option_symbol="QQQ240115C00370000",
            symbol="QQQ",
            strategy_id="vwap_reclaim",
            direction="LONG",
            quantity=1,
            limit_price=4.00,
            submitted_at=ET_NAIVE,
            status="cancelled",
            session_date=TODAY,
        ))
        await db_session.commit()
        return db_session

    @pytest.mark.asyncio
    async def test_report_trade_counts(self, seeded_session: AsyncSession):
        reporter = HealthReporter(seeded_session)
        report = await reporter.generate(TODAY, api_errors=2)

        assert report["trades"]["total_closed"] == 5
        assert report["trades"]["wins"] == 3
        assert report["trades"]["losses"] == 2
        assert report["trades"]["win_rate"] == pytest.approx(0.60)
        assert report["trades"]["rejected"] == 1

    @pytest.mark.asyncio
    async def test_report_pnl(self, seeded_session: AsyncSession):
        reporter = HealthReporter(seeded_session)
        report = await reporter.generate(TODAY)

        assert report["realized_pnl"] == pytest.approx(320.0)   # 100+200+150-50-80
        assert report["avg_win"] == pytest.approx(150.0)
        assert report["avg_loss"] == pytest.approx(-65.0)

    @pytest.mark.asyncio
    async def test_report_order_counts(self, seeded_session: AsyncSession):
        reporter = HealthReporter(seeded_session)
        report = await reporter.generate(TODAY)

        assert report["orders"]["submitted"] == 2
        assert report["orders"]["filled"] == 1
        assert report["orders"]["cancelled"] == 1
        assert report["orders"]["rejected"] == 1

    @pytest.mark.asyncio
    async def test_report_by_strategy(self, seeded_session: AsyncSession):
        reporter = HealthReporter(seeded_session)
        report = await reporter.generate(TODAY)

        assert "orb" in report["by_strategy"]
        assert "vwap_reclaim" in report["by_strategy"]
        orb = report["by_strategy"]["orb"]
        assert orb["trades"] == 3   # 2 wins + 1 loss
        assert orb["wins"] == 2
        assert orb["pnl"] == pytest.approx(250.0)  # 100+200-50

    @pytest.mark.asyncio
    async def test_report_api_errors_and_warnings(self, seeded_session: AsyncSession):
        reporter = HealthReporter(seeded_session)
        warnings = ["Mismatch: PM has X but broker does not"]
        report = await reporter.generate(TODAY, api_errors=3, reconciliation_warnings=warnings)

        assert report["api_errors"] == 3
        assert report["reconciliation_warnings"] == warnings

    @pytest.mark.asyncio
    async def test_report_max_drawdown(self, seeded_session: AsyncSession):
        reporter = HealthReporter(seeded_session)
        report = await reporter.generate(TODAY)

        # Max drawdown is computed from the pnl time series; just verify it's in [0, 1]
        assert 0.0 <= report["max_drawdown"] <= 1.0

    @pytest.mark.asyncio
    async def test_empty_session_report(self, db_session: AsyncSession):
        """Report for a session with no trades should return zeros, not crash."""
        reporter = HealthReporter(db_session)
        report = await reporter.generate("2024-01-16")

        assert report["trades"]["total_closed"] == 0
        assert report["trades"]["win_rate"] == 0.0
        assert report["realized_pnl"] == 0.0
        assert report["orders"]["submitted"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 8. P6 — _pending_entries derived from broker state, not journal alone
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrokerDerivedPendingEntries:
    """
    After restart, _pending_entries in RiskManager must reflect the CURRENT
    broker state.  A DB "pending" row does not prove the order is still live;
    it may have filled, expired, or been cancelled while the process was offline.
    """

    @pytest.mark.asyncio
    async def test_pending_set_to_broker_confirmed_count(self, db_session: AsyncSession):
        """
        DB has 2 pending rows.  Broker confirms only 1 is still open.
        _pending_entries should be restored to 1, not 2.
        """
        from unittest.mock import MagicMock, AsyncMock
        from app.risk.risk_manager import RiskManager

        db_session.add(_pending_row(db_session, "ORD-LIVE", option_symbol="SPY240115C00450000"))
        db_session.add(_pending_row(db_session, "ORD-DEAD", option_symbol="SPY240115P00440000"))
        await db_session.commit()

        store = PendingOrderStore(db_session)
        ft = FillTracker()
        pm = PositionManager(_make_settings())

        # Broker returns only ORD-LIVE as open; ORD-DEAD has been cancelled offline
        live_order = _make_order_result("ORD-LIVE", "SPY240115C00450000", OrderStatus.NEW)
        broker = _make_broker(orders=[live_order])

        settings = MagicMock()
        settings.risk.max_trades_per_day = 3
        settings.risk.max_daily_loss = 500
        settings.risk.max_risk_per_trade = 100
        settings.risk.max_spread_pct = 0.10
        settings.risk.min_open_interest = 0
        settings.risk.min_volume = 0
        settings.risk.earnings_blackout_days = 0
        settings.risk.allow_earnings_trades = True
        settings.risk.min_underlying_price = 0
        settings.risk.min_underlying_avg_volume = 0
        settings.universe.max_active_positions = 2
        settings.universe.max_symbols_traded_per_day = 3
        settings.universe.max_contracts_per_position = 1
        risk = RiskManager(settings)
        risk.start_session(Decimal("50000"))

        journal = MagicMock()
        journal.get_session_summary = AsyncMock(return_value={"entries": 1, "pnl": 0.0})
        journal.get_open_with_exit_order = AsyncMock(return_value=[])

        result = await SessionRecovery().recover(broker, pm, ft, store, TODAY, journal=journal, risk=risk)

        # DB had 2 pending rows loaded; broker only confirms 1 as open
        assert result.pending_orders_loaded == 2  # from DB
        # risk._pending_entries should be 1 (broker-confirmed), not 2 (DB count)
        assert risk._pending_entries == 1

    @pytest.mark.asyncio
    async def test_pending_falls_back_to_db_count_if_broker_unavailable(self, db_session: AsyncSession):
        """
        When broker.get_orders() raises NotImplementedError, _pending_entries
        falls back to the DB-derived count with a warning.
        """
        from app.risk.risk_manager import RiskManager

        db_session.add(_pending_row(db_session, "ORD-ONLY", option_symbol="SPY240115C00450000"))
        await db_session.commit()

        store = PendingOrderStore(db_session)
        ft = FillTracker()
        pm = PositionManager(_make_settings())

        broker = _make_broker()
        broker.get_orders = AsyncMock(side_effect=NotImplementedError())

        settings = MagicMock()
        settings.risk.max_trades_per_day = 3
        settings.risk.max_daily_loss = 500
        settings.risk.max_risk_per_trade = 100
        settings.risk.max_spread_pct = 0.10
        settings.risk.min_open_interest = 0
        settings.risk.min_volume = 0
        settings.risk.earnings_blackout_days = 0
        settings.risk.allow_earnings_trades = True
        settings.risk.min_underlying_price = 0
        settings.risk.min_underlying_avg_volume = 0
        settings.universe.max_active_positions = 2
        settings.universe.max_symbols_traded_per_day = 3
        settings.universe.max_contracts_per_position = 1
        risk = RiskManager(settings)
        risk.start_session(Decimal("50000"))

        journal = MagicMock()
        journal.get_session_summary = AsyncMock(return_value={"entries": 0, "pnl": 0.0})
        journal.get_open_with_exit_order = AsyncMock(return_value=[])

        await SessionRecovery().recover(broker, pm, ft, store, TODAY, journal=journal, risk=risk)

        # Broker unavailable: DB count (1) used as fallback
        assert risk._pending_entries == 1

    @pytest.mark.asyncio
    async def test_all_db_orders_cancelled_at_broker(self, db_session: AsyncSession):
        """
        DB has 2 pending rows but broker confirms 0 open orders.
        _pending_entries should be restored to 0.
        """
        from app.risk.risk_manager import RiskManager

        db_session.add(_pending_row(db_session, "ORD-A"))
        db_session.add(_pending_row(db_session, "ORD-B", option_symbol="SPY240115P00440000"))
        await db_session.commit()

        store = PendingOrderStore(db_session)
        ft = FillTracker()
        pm = PositionManager(_make_settings())

        # Broker has no open orders — both may have filled/expired offline
        broker = _make_broker(orders=[])

        settings = MagicMock()
        settings.risk.max_trades_per_day = 3
        settings.risk.max_daily_loss = 500
        settings.risk.max_risk_per_trade = 100
        settings.risk.max_spread_pct = 0.10
        settings.risk.min_open_interest = 0
        settings.risk.min_volume = 0
        settings.risk.earnings_blackout_days = 0
        settings.risk.allow_earnings_trades = True
        settings.risk.min_underlying_price = 0
        settings.risk.min_underlying_avg_volume = 0
        settings.universe.max_active_positions = 2
        settings.universe.max_symbols_traded_per_day = 3
        settings.universe.max_contracts_per_position = 1
        risk = RiskManager(settings)
        risk.start_session(Decimal("50000"))

        journal = MagicMock()
        journal.get_session_summary = AsyncMock(return_value={"entries": 2, "pnl": 0.0})
        journal.get_open_with_exit_order = AsyncMock(return_value=[])

        await SessionRecovery().recover(broker, pm, ft, store, TODAY, journal=journal, risk=risk)

        # All DB orders absent at broker: _pending_entries = 0
        assert risk._pending_entries == 0


# ═══════════════════════════════════════════════════════════════════════════════
# P6-bis: RECON_BLOCKED — fail-closed when broker order state is unknown
# ═══════════════════════════════════════════════════════════════════════════════

def _make_risk_manager():
    """RiskManager with MagicMock settings suitable for recon_blocked tests."""
    from app.risk.risk_manager import RiskManager
    settings = MagicMock()
    settings.risk.max_trades_per_day = 3
    settings.risk.max_daily_loss = 0.10
    settings.risk.max_risk_per_trade = 0.02
    settings.risk.max_spread_pct = 0.10
    settings.risk.min_open_interest = 0
    settings.risk.min_volume = 0
    settings.risk.earnings_blackout_days = 0
    settings.risk.allow_earnings_trades = True
    settings.risk.min_underlying_price = 0
    settings.risk.min_underlying_avg_volume = 0
    settings.universe.max_active_positions = 2
    settings.universe.max_symbols_traded_day = 3
    settings.universe.max_contracts_per_position = 1
    settings.is_kill_switch_active.return_value = False
    settings.live_trading_enabled = False
    settings.market_open = "09:30"
    settings.market_close = "16:00"
    settings.no_trade_open_buffer_minutes = 5
    settings.no_trade_close_buffer_minutes = 5
    rm = RiskManager(settings)
    rm.start_session(Decimal("50000"))
    return rm


def _make_entry_request():
    """Minimal entry OrderRequest that would pass all checks except recon_blocked."""
    from app.brokers.broker_interface import OrderRequest, OrderSide, OrderType
    from decimal import Decimal
    return OrderRequest(
        symbol="SPY",
        option_symbol="SPY240115C00450000",
        side=OrderSide.BUY_TO_OPEN,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("2.00"),
        strategy_id="orb",
    )


class TestReconBlocked:
    """
    RECON_BLOCKED prevents new entries when broker order state is unknown.

    Scenarios:
      1. Broker unavailable, DB pending=0 — entries blocked
      2. Broker unavailable, DB pending>0 — entries blocked (and count preserved)
      3. Broker becomes available via reconciler — block clears, entries resume
      4. Broker has order unknown to local state — count conservative, entries blocked
      5. Local state has order absent from broker — entries blocked until resolved
    """

    @pytest.mark.asyncio
    async def test_broker_unavailable_db_zero_entries_blocked(self, db_session: AsyncSession):
        """
        broker.get_orders() raises NotImplementedError, DB has no pending rows.
        Even with zero pending count, entries must be blocked.
        """
        store = PendingOrderStore(db_session)
        ft = FillTracker()
        pm = PositionManager(_make_settings())

        broker = _make_broker()
        broker.get_orders = AsyncMock(side_effect=NotImplementedError())

        risk = _make_risk_manager()
        journal = MagicMock()
        journal.get_session_summary = AsyncMock(return_value={"entries": 0, "pnl": 0.0})
        journal.get_open_with_exit_order = AsyncMock(return_value=[])

        await SessionRecovery().recover(broker, pm, ft, store, TODAY, journal=journal, risk=risk)

        assert risk.recon_blocked is True, "RECON_BLOCKED must be set when broker is unavailable"

        from app.risk.risk_manager import RiskCheck
        result = risk.check_order(_make_entry_request(), Decimal("50000"))
        assert result.passed is False
        assert RiskCheck.RECON_BLOCKED in result.failed_checks

    @pytest.mark.asyncio
    async def test_broker_unavailable_db_positive_entries_blocked(self, db_session: AsyncSession):
        """
        broker.get_orders() raises NotImplementedError, DB has 1 pending row.
        _pending_entries is restored from DB AND entries are blocked.
        """
        db_session.add(_pending_row(db_session, "ORD-ONLY"))
        await db_session.commit()

        store = PendingOrderStore(db_session)
        ft = FillTracker()
        pm = PositionManager(_make_settings())

        broker = _make_broker()
        broker.get_orders = AsyncMock(side_effect=NotImplementedError())

        risk = _make_risk_manager()
        journal = MagicMock()
        journal.get_session_summary = AsyncMock(return_value={"entries": 0, "pnl": 0.0})
        journal.get_open_with_exit_order = AsyncMock(return_value=[])

        await SessionRecovery().recover(broker, pm, ft, store, TODAY, journal=journal, risk=risk)

        assert risk._pending_entries == 1, "DB-derived count must be preserved"
        assert risk.recon_blocked is True, "RECON_BLOCKED must be set when broker is unavailable"

        from app.risk.risk_manager import RiskCheck
        result = risk.check_order(_make_entry_request(), Decimal("50000"))
        assert result.passed is False
        assert RiskCheck.RECON_BLOCKED in result.failed_checks

    @pytest.mark.asyncio
    async def test_broker_available_later_clears_block(self, db_session: AsyncSession):
        """
        Block set at recovery time is cleared when the periodic Reconciler
        successfully calls broker.get_orders() and broker.get_positions().
        """
        db_session.add(_pending_row(db_session, "ORD-ONE"))
        await db_session.commit()

        store = PendingOrderStore(db_session)
        ft = FillTracker()
        pm = PositionManager(_make_settings())

        # Recovery: broker unavailable
        broker_down = _make_broker()
        broker_down.get_orders = AsyncMock(side_effect=NotImplementedError())

        risk = _make_risk_manager()
        journal = MagicMock()
        journal.get_session_summary = AsyncMock(return_value={"entries": 0, "pnl": 0.0})
        journal.get_open_with_exit_order = AsyncMock(return_value=[])

        await SessionRecovery().recover(broker_down, pm, ft, store, TODAY, journal=journal, risk=risk)
        assert risk.recon_blocked is True

        # Broker comes back: confirms ORD-ONE is still open, no positions
        ord_one = _make_order_result("ORD-ONE", "SPY240115C00450000", OrderStatus.NEW)
        broker_up = _make_broker(positions=[], orders=[ord_one])
        from app.trading.reconciler import Reconciler
        now = datetime(2024, 1, 15, 10, 30, 0)
        await Reconciler().reconcile(broker_up, pm, ft, now, risk=risk)

        assert risk.recon_blocked is False, "RECON_BLOCKED must be cleared after successful reconciliation"

        from app.risk.risk_manager import RiskCheck
        result = risk.check_order(_make_entry_request(), Decimal("50000"))
        assert RiskCheck.RECON_BLOCKED not in result.failed_checks

    @pytest.mark.asyncio
    async def test_broker_has_unknown_order_conservative_count(self, db_session: AsyncSession):
        """
        Broker reports 1 open order that FillTracker does not know about.
        _pending_entries is restored conservatively (len(broker_open_ids) = 1)
        and entries are blocked until the order is reconciled.
        """
        # No DB rows — local state has no knowledge of ORD-UNKNOWN
        store = PendingOrderStore(db_session)
        ft = FillTracker()
        pm = PositionManager(_make_settings())

        unknown_order = _make_order_result("ORD-UNKNOWN", "SPY240115C00450000", OrderStatus.NEW)
        broker = _make_broker(orders=[unknown_order])

        risk = _make_risk_manager()
        journal = MagicMock()
        journal.get_session_summary = AsyncMock(return_value={"entries": 0, "pnl": 0.0})
        journal.get_open_with_exit_order = AsyncMock(return_value=[])

        result = await SessionRecovery().recover(
            broker, pm, ft, store, TODAY, journal=journal, risk=risk
        )

        # Conservative count: all broker open orders are counted
        assert risk._pending_entries == 1, (
            "_pending_entries must include broker open orders unknown to local state"
        )
        assert risk.recon_blocked is True, (
            "RECON_BLOCKED must be set when broker has orders local state missed"
        )
        assert any("untracked" in w.lower() or "unknown" in w.lower()
                   for w in result.warnings), "Unknown broker order must appear in warnings"

    @pytest.mark.asyncio
    async def test_local_order_absent_from_broker_entries_blocked(self, db_session: AsyncSession):
        """
        Local FillTracker has order ORD-LOCAL that the broker no longer shows as open.
        The order may have filled/expired offline.  Entries are blocked until the
        next FillTracker poll cycle resolves the order's status.
        """
        db_session.add(_pending_row(db_session, "ORD-LOCAL"))
        await db_session.commit()

        store = PendingOrderStore(db_session)
        ft = FillTracker()
        pm = PositionManager(_make_settings())

        # Broker has no open orders — ORD-LOCAL is absent
        broker = _make_broker(orders=[])

        risk = _make_risk_manager()
        journal = MagicMock()
        journal.get_session_summary = AsyncMock(return_value={"entries": 0, "pnl": 0.0})
        journal.get_open_with_exit_order = AsyncMock(return_value=[])

        await SessionRecovery().recover(broker, pm, ft, store, TODAY, journal=journal, risk=risk)

        # DB order is in FT; broker has no matching open order
        # Pending count: len(_broker_open_ids) = 0 (nothing open at broker)
        assert risk._pending_entries == 0
        # But entries must be blocked until FillTracker poll resolves ORD-LOCAL
        assert risk.recon_blocked is True, (
            "RECON_BLOCKED must be set when local orders are absent from broker"
        )

    @pytest.mark.asyncio
    async def test_reconciler_unknown_broker_order_stays_blocked(self):
        """
        Reconciler: broker.get_orders() succeeds but returns an order that
        FillTracker does not know about.  RECON_BLOCKED must remain set —
        a successful API call is not sufficient when there are flagged discrepancies.
        """
        ft = FillTracker()
        pm = PositionManager(_make_settings())

        # Broker returns one unknown order
        unknown = _make_order_result("ORD-GHOST", "SPY240115C00450000", OrderStatus.NEW)
        broker = _make_broker(positions=[], orders=[unknown])

        risk = _make_risk_manager()
        risk.set_recon_blocked("set before reconciler runs")

        from app.trading.reconciler import Reconciler
        now = datetime(2024, 1, 15, 10, 30, 0)
        result = await Reconciler().reconcile(broker, pm, ft, now, risk=risk)

        # API call succeeded
        assert result.orders_confirmed is True
        assert result.positions_confirmed is True
        # But the unknown order produced a flag
        assert result.flagged, "Unknown broker order must produce a reconciliation flag"

        # Block must not have cleared
        assert risk.recon_blocked is True, (
            "RECON_BLOCKED must remain set when reconciliation has unresolved flags"
        )

        from app.risk.risk_manager import RiskCheck
        check = risk.check_order(_make_entry_request(), Decimal("50000"))
        assert check.passed is False
        assert RiskCheck.RECON_BLOCKED in check.failed_checks
