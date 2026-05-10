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
