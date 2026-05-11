"""
Tests for the paper-evaluation fill lifecycle:

  - Fill metrics in DailyReport (fill_rate, cancel_rate, avg_spread_at_entry,
    avg_spread_at_exit, time_to_fill_avg_secs, missed_fills_count,
    stop_loss_hit_pct, take_profit_hit_pct, eod_exit_pct)
  - TradeJournal.record_fill sets filled_at + time_to_fill_secs
  - TradeJournal.record_exit stores exit_bid/ask/spread_pct
  - PositionManager: current_price tracking, to_dict_list with derived levels
  - Dashboard GET /session/state returns expected structure
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ET = ZoneInfo("America/New_York")


# ── Shared helpers ────────────────────────────────────────────────────────────

async def _make_db():
    from app.api.models import Base
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


def _make_settings():
    s = MagicMock()
    s.live_trading_enabled = False
    s.paper_evaluation_mode = True
    s.broker = "alpaca"
    s.database_url = "sqlite+aiosqlite:///:memory:"
    s.kill_switch_file = "./KILL_SWITCH_NONEXISTENT"
    s.is_kill_switch_active.return_value = False
    s.market_open = "09:30"
    s.market_close = "16:00"
    s.risk = MagicMock()
    s.risk.max_trades_per_day = 3
    # PositionManager reads settings.position for exit thresholds
    s.position = MagicMock()
    s.position.stop_loss_pct = 0.50
    s.position.take_profit_pct = 1.00
    s.position.trailing_stop_pct = 0.25
    s.position.max_hold_minutes = 120
    s.position.eod_exit_time = "15:45"
    s.position.cooldown_after_loss_minutes = 15
    return s


# ── DailyReport: fill efficiency metrics ─────────────────────────────────────

class TestDailyReportFillMetrics:

    async def _make_report(self, trades):
        from app.evaluation.daily_report import build_daily_report
        factory, engine = await _make_db()
        session_date = "2026-06-01"
        async with factory() as session:
            for t in trades:
                session.add(t)
            await session.commit()
            report = await build_daily_report(session, session_date)
        await engine.dispose()
        return report

    def _closed(self, idx, *, pnl=10.0, exit_reason="take_profit",
                spread_pct=0.05, exit_spread_pct=None, time_to_fill_secs=None):
        from app.api.models import DBTradeJournal
        now = datetime(2026, 6, 1, 10, 0, tzinfo=ET)
        t = DBTradeJournal(
            session_date="2026-06-01",
            strategy_id="orb",
            underlying_symbol="SPY",
            option_symbol=f"SPY260601C{idx:08d}",
            status="closed",
            fill_price=2.00,
            limit_price=1.95,
            exit_price=2.50,
            realized_pnl=pnl,
            slippage=0.05,
            bid=1.90,
            ask=2.10,
            spread_pct=spread_pct,
            exit_spread_pct=exit_spread_pct,
            time_to_fill_secs=time_to_fill_secs,
            exit_reason=exit_reason,
            quantity=1,
            filled_quantity=1,
            entry_time=now,
            exit_time=now + timedelta(minutes=30),
            is_paper=True,
        )
        return t

    def _cancelled(self, idx, *, fill_price=None, spread_pct=0.08):
        from app.api.models import DBTradeJournal
        now = datetime(2026, 6, 1, 10, 0, tzinfo=ET)
        return DBTradeJournal(
            session_date="2026-06-01",
            strategy_id="orb",
            underlying_symbol="SPY",
            option_symbol=f"SPY260601C9{idx:07d}",
            status="cancelled",
            fill_price=fill_price,
            limit_price=1.95,
            spread_pct=spread_pct,
            quantity=1,
            entry_time=now,
            is_paper=True,
        )

    @pytest.mark.asyncio
    async def test_fill_rate_two_fills_one_cancel(self):
        trades = [self._closed(1), self._closed(2), self._cancelled(3)]
        report = await self._make_report(trades)
        assert report.fill_rate == pytest.approx(2 / 3, abs=0.001)

    @pytest.mark.asyncio
    async def test_cancel_rate_two_fills_one_cancel(self):
        trades = [self._closed(1), self._closed(2), self._cancelled(3)]
        report = await self._make_report(trades)
        assert report.cancel_rate == pytest.approx(1 / 3, abs=0.001)

    @pytest.mark.asyncio
    async def test_fill_cancel_rate_none_when_zero_submitted(self):
        report = await self._make_report([])
        assert report.fill_rate is None
        assert report.cancel_rate is None

    @pytest.mark.asyncio
    async def test_missed_fills_count_cancelled_without_fill(self):
        trades = [
            self._cancelled(1, fill_price=None),   # missed fill
            self._cancelled(2, fill_price=2.00),   # partial fill then cancelled
            self._closed(3),
        ]
        report = await self._make_report(trades)
        assert report.missed_fills_count == 1

    @pytest.mark.asyncio
    async def test_avg_spread_at_entry(self):
        trades = [
            self._closed(1, spread_pct=0.04),
            self._closed(2, spread_pct=0.06),
            self._cancelled(3, spread_pct=0.08),
        ]
        report = await self._make_report(trades)
        # avg of all submitted: (0.04 + 0.06 + 0.08) / 3
        assert report.avg_spread_at_entry == pytest.approx(0.06, abs=0.001)

    @pytest.mark.asyncio
    async def test_avg_spread_at_exit(self):
        trades = [
            self._closed(1, exit_spread_pct=0.03),
            self._closed(2, exit_spread_pct=0.07),
        ]
        report = await self._make_report(trades)
        assert report.avg_spread_at_exit == pytest.approx(0.05, abs=0.001)

    @pytest.mark.asyncio
    async def test_avg_spread_at_exit_none_when_not_recorded(self):
        trades = [self._closed(1, exit_spread_pct=None)]
        report = await self._make_report(trades)
        assert report.avg_spread_at_exit is None

    @pytest.mark.asyncio
    async def test_time_to_fill_avg_secs(self):
        trades = [
            self._closed(1, time_to_fill_secs=45.0),
            self._closed(2, time_to_fill_secs=75.0),
        ]
        report = await self._make_report(trades)
        assert report.time_to_fill_avg_secs == pytest.approx(60.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_stop_loss_hit_pct(self):
        trades = [
            self._closed(1, exit_reason="stop_loss"),
            self._closed(2, exit_reason="take_profit"),
            self._closed(3, exit_reason="take_profit"),
        ]
        report = await self._make_report(trades)
        assert report.stop_loss_hit_pct == pytest.approx(1 / 3, abs=0.001)

    @pytest.mark.asyncio
    async def test_take_profit_hit_pct(self):
        trades = [
            self._closed(1, exit_reason="stop_loss"),
            self._closed(2, exit_reason="take_profit"),
            self._closed(3, exit_reason="take_profit"),
        ]
        report = await self._make_report(trades)
        assert report.take_profit_hit_pct == pytest.approx(2 / 3, abs=0.001)

    @pytest.mark.asyncio
    async def test_eod_exit_pct(self):
        trades = [
            self._closed(1, exit_reason="eod_exit"),
            self._closed(2, exit_reason="take_profit"),
        ]
        report = await self._make_report(trades)
        assert report.eod_exit_pct == pytest.approx(0.5, abs=0.001)

    @pytest.mark.asyncio
    async def test_exit_reason_pct_none_when_no_fills(self):
        trades = [self._cancelled(1)]
        report = await self._make_report(trades)
        assert report.stop_loss_hit_pct is None
        assert report.take_profit_hit_pct is None
        assert report.eod_exit_pct is None


# ── TradeJournal: record_fill sets filled_at + time_to_fill_secs ──────────────

class TestTradeJournalFillTiming:

    @pytest.mark.asyncio
    async def test_record_fill_sets_filled_at(self):
        from app.api.models import DBTradeJournal
        from app.trading.trade_journal import TradeJournal
        factory, engine = await _make_db()
        session_date = "2026-06-01"
        async with factory() as session:
            entry_time = datetime(2026, 6, 1, 9, 45, tzinfo=ET)
            row = DBTradeJournal(
                session_date=session_date,
                strategy_id="orb",
                underlying_symbol="SPY",
                option_symbol="SPY260601C00740000",
                status="open",
                limit_price=2.00,
                quantity=1,
                entry_time=entry_time,
                is_paper=True,
            )
            session.add(row)
            await session.flush()
            journal_id = row.id

            journal = TradeJournal(session, is_paper=True)
            await journal.record_fill(journal_id, fill_price=2.05, filled_quantity=1)
            await session.commit()

            refreshed = await session.get(DBTradeJournal, journal_id)
            assert refreshed.filled_at is not None
            assert refreshed.fill_price == pytest.approx(2.05)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_record_fill_sets_time_to_fill(self):
        from app.api.models import DBTradeJournal
        from app.trading.trade_journal import TradeJournal
        factory, engine = await _make_db()
        session_date = "2026-06-01"
        async with factory() as session:
            entry_time = datetime(2026, 6, 1, 9, 45, tzinfo=ET)
            row = DBTradeJournal(
                session_date=session_date,
                strategy_id="orb",
                underlying_symbol="SPY",
                option_symbol="SPY260601C00740001",
                status="open",
                limit_price=2.00,
                quantity=1,
                entry_time=entry_time,
                is_paper=True,
            )
            session.add(row)
            await session.flush()

            journal = TradeJournal(session, is_paper=True)
            await journal.record_fill(row.id, fill_price=2.05, filled_quantity=1)
            await session.commit()

            refreshed = await session.get(DBTradeJournal, row.id)
            assert refreshed.time_to_fill_secs is not None
            assert refreshed.time_to_fill_secs >= 0.0
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_record_fill_none_journal_id_is_safe(self):
        from app.trading.trade_journal import TradeJournal
        factory, engine = await _make_db()
        async with factory() as session:
            journal = TradeJournal(session, is_paper=True)
            # Should not raise even for non-existent id
            await journal.record_fill(9999, fill_price=2.05, filled_quantity=1)
        await engine.dispose()


# ── TradeJournal: record_exit stores exit spread ──────────────────────────────

class TestTradeJournalExitSpread:

    @pytest.mark.asyncio
    async def test_record_exit_stores_exit_bid_ask_spread(self):
        from app.api.models import DBTradeJournal
        from app.trading.trade_journal import TradeJournal
        factory, engine = await _make_db()
        async with factory() as session:
            entry_time = datetime(2026, 6, 1, 9, 45, tzinfo=ET)
            row = DBTradeJournal(
                session_date="2026-06-01",
                strategy_id="orb",
                underlying_symbol="SPY",
                option_symbol="SPY260601C00740000",
                status="open",
                fill_price=2.00,
                limit_price=1.95,
                quantity=1,
                entry_time=entry_time,
                is_paper=True,
            )
            session.add(row)
            await session.flush()

            journal = TradeJournal(session, is_paper=True)
            exit_time = datetime(2026, 6, 1, 10, 30, tzinfo=ET)
            await journal.record_exit(
                journal_id=row.id,
                exit_time=exit_time,
                exit_price=2.50,
                exit_reason="take_profit",
                realized_pnl=50.0,
                hold_duration_secs=2700.0,
                exit_bid=2.45,
                exit_ask=2.55,
            )
            await session.commit()

            refreshed = await session.get(DBTradeJournal, row.id)
            assert refreshed.exit_bid == pytest.approx(2.45)
            assert refreshed.exit_ask == pytest.approx(2.55)
            # exit_spread_pct = (2.55 - 2.45) / 2.50 = 0.04
            assert refreshed.exit_spread_pct == pytest.approx(0.04, abs=0.001)
            assert refreshed.status == "closed"
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_record_exit_without_spread_leaves_spread_none(self):
        from app.api.models import DBTradeJournal
        from app.trading.trade_journal import TradeJournal
        factory, engine = await _make_db()
        async with factory() as session:
            row = DBTradeJournal(
                session_date="2026-06-01",
                strategy_id="orb",
                underlying_symbol="SPY",
                option_symbol="SPY260601C00740002",
                status="open",
                quantity=1,
                is_paper=True,
            )
            session.add(row)
            await session.flush()

            journal = TradeJournal(session, is_paper=True)
            await journal.record_exit(
                journal_id=row.id,
                exit_time=datetime(2026, 6, 1, 10, 30, tzinfo=ET),
                exit_price=2.50,
                exit_reason="stop_loss",
                realized_pnl=-20.0,
                hold_duration_secs=1800.0,
            )
            await session.commit()

            refreshed = await session.get(DBTradeJournal, row.id)
            assert refreshed.exit_spread_pct is None
        await engine.dispose()


# ── PositionManager: current_price and derived levels ────────────────────────

class TestPositionManagerCurrentPrice:

    def _make_pm(self):
        from app.trading.position_manager import PositionManager
        s = MagicMock()
        s.stop_loss_pct = 0.50
        s.take_profit_pct = 1.00
        s.trailing_stop_pct = 0.25
        s.max_hold_minutes = 120
        s.eod_exit_time = "15:45"
        s.cooldown_after_loss_minutes = 15
        settings = MagicMock()
        settings.position = s
        return PositionManager(settings)

    def test_update_price_sets_current_price(self):
        pm = self._make_pm()
        pm.open(
            option_symbol="SPY260601C00740000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            entry_time=datetime(2026, 6, 1, 9, 45, tzinfo=ET),
            entry_price=2.00,
            quantity=1,
        )
        pm.update_price("SPY260601C00740000", 2.50)
        pos = pm._positions["SPY260601C00740000"]
        assert pos.current_price == pytest.approx(2.50)

    def test_update_price_tracks_peak(self):
        pm = self._make_pm()
        pm.open(
            option_symbol="SPY260601C00740000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            entry_time=datetime(2026, 6, 1, 9, 45, tzinfo=ET),
            entry_price=2.00,
            quantity=1,
        )
        pm.update_price("SPY260601C00740000", 3.00)
        pm.update_price("SPY260601C00740000", 2.80)  # price fell, peak stays
        pos = pm._positions["SPY260601C00740000"]
        assert pos.peak_price == pytest.approx(3.00)
        assert pos.current_price == pytest.approx(2.80)

    def test_to_dict_list_includes_derived_levels(self):
        pm = self._make_pm()
        entry_price = 2.00
        pm.open(
            option_symbol="SPY260601C00740000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            entry_time=datetime(2026, 6, 1, 9, 45, tzinfo=ET),
            entry_price=entry_price,
            quantity=1,
        )
        pm.update_price("SPY260601C00740000", 2.50)
        result = pm.to_dict_list()
        assert len(result) == 1
        d = result[0]
        # stop_loss_level = 2.00 * (1 - 0.50) = 1.00
        assert d["stop_loss_level"] == pytest.approx(1.00, abs=0.001)
        # take_profit_level = 2.00 * (1 + 1.00) = 4.00
        assert d["take_profit_level"] == pytest.approx(4.00, abs=0.001)
        # trailing_stop_level = peak(2.50) * (1 - 0.25) = 1.875
        assert d["trailing_stop_level"] == pytest.approx(1.875, abs=0.001)

    def test_to_dict_list_unrealized_pnl(self):
        pm = self._make_pm()
        pm.open(
            option_symbol="SPY260601C00740000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            entry_time=datetime(2026, 6, 1, 9, 45, tzinfo=ET),
            entry_price=2.00,
            quantity=1,
        )
        pm.update_price("SPY260601C00740000", 2.50)
        d = pm.to_dict_list()[0]
        # unrealized_pnl = (2.50 - 2.00) * 100 * 1 = 50.00
        assert d["unrealized_pnl"] == pytest.approx(50.00, abs=0.01)

    def test_to_dict_list_entry_price_used_when_no_quote(self):
        pm = self._make_pm()
        pm.open(
            option_symbol="SPY260601C00740000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            entry_time=datetime(2026, 6, 1, 9, 45, tzinfo=ET),
            entry_price=2.00,
            quantity=1,
        )
        # No update_price call — current_price defaults to entry_price
        d = pm.to_dict_list()[0]
        assert d["current_price"] == pytest.approx(2.00)
        assert d["unrealized_pnl"] == pytest.approx(0.0)

    def test_to_dict_list_hold_minutes_positive(self):
        pm = self._make_pm()
        past = datetime.now(tz=ET) - timedelta(minutes=15)
        pm.open(
            option_symbol="SPY260601C00740000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            entry_time=past,
            entry_price=2.00,
            quantity=1,
        )
        d = pm.to_dict_list()[0]
        assert d["hold_minutes"] >= 14.0  # at least ~15 min

    def test_empty_positions_returns_empty_list(self):
        pm = self._make_pm()
        assert pm.to_dict_list() == []


# ── Dashboard: /session/state endpoint ───────────────────────────────────────

@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    s = _make_settings()
    monkeypatch.setattr("app.config.get_settings", lambda: s)
    monkeypatch.setattr("app.api.models.get_settings", lambda: s, raising=False)
    monkeypatch.setattr("app.api.dashboard_api.get_settings", lambda: s, raising=False)
    return s


@pytest_asyncio.fixture
async def memory_engine():
    from app.api.models import Base
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_client(memory_engine):
    from app.api.dashboard_api import create_app
    from app.api.models import get_db

    app = create_app()
    factory = async_sessionmaker(memory_engine, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def session_client_with_pm(memory_engine, _patch_settings):
    from app.api.dashboard_api import create_app
    from app.api.models import get_db
    from app.trading.position_manager import PositionManager

    pm = PositionManager(_patch_settings)
    app = create_app(position_manager=pm)
    factory = async_sessionmaker(memory_engine, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac, pm


class TestSessionStateEndpoint:

    @pytest.mark.asyncio
    async def test_session_state_returns_200(self, session_client):
        resp = await session_client.get("/session/state")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_session_state_required_keys(self, session_client):
        data = (await session_client.get("/session/state")).json()
        required = {
            "paper_mode", "paper_evaluation_mode", "kill_switch_active",
            "trades_today", "max_trades_per_day", "trades_remaining",
            "daily_pnl", "unrealized_pnl_total", "total_pnl",
            "open_positions_count", "open_positions", "pending_orders_count",
            "now_et", "session_date",
        }
        for key in required:
            assert key in data, f"Missing key: {key}"

    @pytest.mark.asyncio
    async def test_session_state_paper_mode_true(self, session_client):
        data = (await session_client.get("/session/state")).json()
        assert data["paper_mode"] is True
        assert data["paper_evaluation_mode"] is True

    @pytest.mark.asyncio
    async def test_session_state_no_positions_initially(self, session_client):
        data = (await session_client.get("/session/state")).json()
        assert data["open_positions_count"] == 0
        assert data["open_positions"] == []

    @pytest.mark.asyncio
    async def test_session_state_reflects_open_position(
        self, session_client_with_pm
    ):
        client, pm = session_client_with_pm
        pm.open(
            option_symbol="SPY260601C00740000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            entry_time=datetime(2026, 6, 1, 9, 45, tzinfo=ET),
            entry_price=2.00,
            quantity=1,
        )
        pm.update_price("SPY260601C00740000", 2.30)
        data = (await client.get("/session/state")).json()
        assert data["open_positions_count"] == 1
        pos = data["open_positions"][0]
        assert pos["option_symbol"] == "SPY260601C00740000"
        assert pos["unrealized_pnl"] == pytest.approx(30.0, abs=0.01)
        assert "stop_loss_level" in pos
        assert "take_profit_level" in pos
        assert "trailing_stop_level" in pos

    @pytest.mark.asyncio
    async def test_session_state_unrealized_pnl_total(
        self, session_client_with_pm
    ):
        client, pm = session_client_with_pm
        pm.open(
            option_symbol="SPY260601C00740000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            entry_time=datetime(2026, 6, 1, 9, 45, tzinfo=ET),
            entry_price=2.00,
            quantity=1,
        )
        pm.update_price("SPY260601C00740000", 2.50)  # +$50 unrealized
        data = (await client.get("/session/state")).json()
        assert data["unrealized_pnl_total"] == pytest.approx(50.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_session_state_trades_remaining(self, session_client, _patch_settings):
        _patch_settings.risk.max_trades_per_day = 3
        # No risk manager wired → trades_today=0
        data = (await session_client.get("/session/state")).json()
        assert data["trades_remaining"] == 3

    @pytest.mark.asyncio
    async def test_positions_open_endpoint_returns_derived_levels(
        self, session_client_with_pm
    ):
        client, pm = session_client_with_pm
        pm.open(
            option_symbol="SPY260601C00740000",
            symbol="SPY",
            strategy_id="orb",
            direction="LONG",
            entry_time=datetime(2026, 6, 1, 9, 45, tzinfo=ET),
            entry_price=4.00,
            quantity=1,
        )
        pm.update_price("SPY260601C00740000", 5.00)
        data = (await client.get("/positions/open")).json()
        assert len(data) == 1
        d = data[0]
        assert "stop_loss_level" in d
        assert "take_profit_level" in d
        assert "trailing_stop_level" in d
        assert "unrealized_pnl" in d
        assert "current_price" in d
        # stop_loss_level = 4.00 * 0.50 = 2.00
        assert d["stop_loss_level"] == pytest.approx(2.00, abs=0.001)


# ── Pricing modes: compute_limit_price ───────────────────────────────────────

class TestPricingModes:

    def setup_method(self):
        from app.trading.pricing import compute_limit_price
        self.clp = compute_limit_price

    def test_bid_mode_returns_bid(self):
        assert self.clp("bid", bid=1.00, ask=1.20) == pytest.approx(1.00)

    def test_mid_mode_returns_midpoint(self):
        assert self.clp("mid", bid=1.00, ask=1.20) == pytest.approx(1.10)

    def test_ask_mode_returns_ask(self):
        assert self.clp("ask", bid=1.00, ask=1.20) == pytest.approx(1.20)

    def test_marketable_limit_above_ask(self):
        # marketable_limit = ask + spread * offset_pct = 1.20 + 0.20 * 0.01 = 1.202 → 1.20
        result = self.clp("marketable_limit", bid=1.00, ask=1.20, offset_pct=0.01)
        assert result >= 1.20
        assert result == pytest.approx(1.20, abs=0.01)

    def test_marketable_limit_wide_spread(self):
        # spread = 0.50, offset adds 0.50 * 0.10 = 0.05 above ask
        result = self.clp("marketable_limit", bid=1.00, ask=1.50, offset_pct=0.10)
        assert result == pytest.approx(1.55, abs=0.01)

    def test_unknown_mode_falls_back_to_mid(self):
        result = self.clp("turbo", bid=1.00, ask=1.20)
        assert result == pytest.approx(1.10)

    def test_returns_two_decimal_places(self):
        result = self.clp("mid", bid=1.001, ask=1.009)
        # midpoint = 1.005 → rounded to 2dp = 1.01
        assert result == round(result, 2)

    def test_all_modes_positive_for_positive_inputs(self):
        for mode in ("bid", "mid", "ask", "marketable_limit"):
            assert self.clp(mode, bid=0.50, ask=0.80) > 0


# ── Settings: realistic_fill_test_mode fields ────────────────────────────────

class TestRealisticFillTestModeSettings:

    def test_default_realistic_fill_test_mode_is_false(self, monkeypatch):
        monkeypatch.delenv("REALISTIC_FILL_TEST_MODE", raising=False)
        monkeypatch.delenv("ENTRY_ORDER_TIMEOUT_SECS", raising=False)
        from app.config.settings import Settings
        s = Settings()
        assert s.realistic_fill_test_mode is False

    def test_entry_order_timeout_default(self, monkeypatch):
        monkeypatch.delenv("ENTRY_ORDER_TIMEOUT_SECS", raising=False)
        from app.config.settings import Settings
        s = Settings()
        assert s.entry_order_timeout_secs == 120

    def test_exit_order_timeout_default(self, monkeypatch):
        monkeypatch.delenv("EXIT_ORDER_TIMEOUT_SECS", raising=False)
        from app.config.settings import Settings
        s = Settings()
        assert s.exit_order_timeout_secs == 120

    def test_fill_test_max_spread_pct_default(self, monkeypatch):
        monkeypatch.delenv("FILL_TEST_MAX_SPREAD_PCT", raising=False)
        from app.config.settings import Settings
        s = Settings()
        assert s.fill_test_max_spread_pct == pytest.approx(0.20)

    def test_entry_limit_price_mode_default(self, monkeypatch):
        monkeypatch.delenv("OPTIONS_ENTRY_LIMIT_PRICE_MODE", raising=False)
        from app.config.settings import OptionsSettings
        s = OptionsSettings()
        assert s.entry_limit_price_mode == "mid"

    def test_exit_limit_price_mode_default(self, monkeypatch):
        monkeypatch.delenv("OPTIONS_EXIT_LIMIT_PRICE_MODE", raising=False)
        from app.config.settings import OptionsSettings
        s = OptionsSettings()
        assert s.exit_limit_price_mode == "mid"

    def test_entry_marketable_offset_pct_default(self, monkeypatch):
        monkeypatch.delenv("OPTIONS_ENTRY_MARKETABLE_OFFSET_PCT", raising=False)
        from app.config.settings import OptionsSettings
        s = OptionsSettings()
        assert s.entry_marketable_offset_pct == pytest.approx(0.01)


# ── Order status telemetry: FillTracker writes DBOrderStatusTransition ────────

class TestOrderStatusTelemetry:

    @pytest.mark.asyncio
    async def test_fill_tracker_records_status_transition(self):
        from app.api.models import Base, DBOrderStatusTransition, DBTradeJournal
        from app.trading.fill_tracker import FillTracker
        from app.trading.position_manager import PositionManager
        from app.trading.trade_journal import TradeJournal
        from app.brokers.broker_interface import OrderResult, OrderStatus, OrderSide

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as session:
            # Insert a journal row
            row = DBTradeJournal(
                session_date="2026-06-01",
                strategy_id="orb",
                underlying_symbol="SPY",
                option_symbol="SPY260601C00740000",
                status="open",
                limit_price=2.00,
                quantity=1,
                entry_time=datetime(2026, 6, 1, 9, 45, tzinfo=ET),
                is_paper=True,
            )
            session.add(row)
            await session.flush()
            journal_id = row.id

            journal = TradeJournal(session, is_paper=True)
            pm_settings = MagicMock()
            pm_settings.stop_loss_pct = 0.50
            pm_settings.take_profit_pct = 1.00
            pm_settings.trailing_stop_pct = 0.25
            pm_settings.max_hold_minutes = 120
            pm_settings.eod_exit_time = "15:45"
            pm_settings.cooldown_after_loss_minutes = 15
            full_settings = MagicMock()
            full_settings.position = pm_settings
            pm = PositionManager(full_settings)

            ft = FillTracker(max_age_minutes=30)
            ft.register(
                order_id="order-001",
                journal_id=journal_id,
                option_symbol="SPY260601C00740000",
                symbol="SPY",
                strategy_id="orb",
                direction="LONG",
                quantity=1,
                limit_price=2.00,
                placed_at=datetime(2026, 6, 1, 9, 45, tzinfo=ET),
            )

            # Mock broker returning NEW status first
            mock_broker = AsyncMock()
            mock_broker.get_order_status.return_value = OrderResult(
                order_id="order-001",
                status=OrderStatus.NEW,
                symbol="SPY",
                option_symbol="SPY260601C00740000",
                side=OrderSide.BUY_TO_OPEN,
                quantity=1,
                limit_price=2.00,
            )
            now = datetime(2026, 6, 1, 9, 46, tzinfo=ET)
            await ft.poll(mock_broker, pm, journal, now)
            await session.commit()

            # Check telemetry row was written
            from sqlalchemy import select
            transitions = (
                await session.execute(
                    select(DBOrderStatusTransition)
                    .where(DBOrderStatusTransition.order_id == "order-001")
                )
            ).scalars().all()
            assert len(transitions) >= 1
            t = transitions[0]
            assert t.status == "new"
            assert t.prev_status == "pending"
            assert t.option_symbol == "SPY260601C00740000"
            assert t.journal_id == journal_id

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_fill_tracker_does_not_duplicate_same_status(self):
        from app.api.models import Base, DBOrderStatusTransition, DBTradeJournal
        from app.trading.fill_tracker import FillTracker
        from app.trading.position_manager import PositionManager
        from app.trading.trade_journal import TradeJournal
        from app.brokers.broker_interface import OrderResult, OrderStatus, OrderSide

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as session:
            row = DBTradeJournal(
                session_date="2026-06-01",
                strategy_id="orb",
                underlying_symbol="SPY",
                option_symbol="SPY260601C00740001",
                status="open",
                limit_price=2.00,
                quantity=1,
                entry_time=datetime(2026, 6, 1, 9, 45, tzinfo=ET),
                is_paper=True,
            )
            session.add(row)
            await session.flush()

            journal = TradeJournal(session, is_paper=True)
            pm_settings = MagicMock()
            pm_settings.stop_loss_pct = 0.50
            pm_settings.take_profit_pct = 1.00
            pm_settings.trailing_stop_pct = 0.25
            pm_settings.max_hold_minutes = 120
            pm_settings.eod_exit_time = "15:45"
            pm_settings.cooldown_after_loss_minutes = 15
            full_settings = MagicMock()
            full_settings.position = pm_settings
            pm = PositionManager(full_settings)

            ft = FillTracker(max_age_minutes=30)
            ft.register(
                order_id="order-002",
                journal_id=row.id,
                option_symbol="SPY260601C00740001",
                symbol="SPY",
                strategy_id="orb",
                direction="LONG",
                quantity=1,
                limit_price=2.00,
                placed_at=datetime(2026, 6, 1, 9, 45, tzinfo=ET),
            )

            mock_broker = AsyncMock()
            mock_broker.get_order_status.return_value = OrderResult(
                order_id="order-002",
                status=OrderStatus.NEW,
                symbol="SPY",
                option_symbol="SPY260601C00740001",
                side=OrderSide.BUY_TO_OPEN,
                quantity=1,
                limit_price=2.00,
            )
            now = datetime(2026, 6, 1, 9, 46, tzinfo=ET)

            # Poll twice with same status — should record transition only once
            await ft.poll(mock_broker, pm, journal, now)
            await ft.poll(mock_broker, pm, journal, now)
            await session.commit()

            from sqlalchemy import select
            transitions = (
                await session.execute(
                    select(DBOrderStatusTransition)
                    .where(DBOrderStatusTransition.order_id == "order-002")
                )
            ).scalars().all()
            # Only one transition: pending → new  (not duplicated on second poll)
            assert len(transitions) == 1

        await engine.dispose()


# ── DailyReport: extended reporting (fill by mode, cancellation breakdown) ────

class TestDailyReportExtended:

    async def _make_report(self, trades):
        from app.evaluation.daily_report import build_daily_report
        factory, engine = await _make_db()
        session_date = "2026-06-01"
        async with factory() as session:
            for t in trades:
                session.add(t)
            await session.commit()
            report = await build_daily_report(session, session_date)
        await engine.dispose()
        return report

    def _trade(self, idx, *, status="closed", mode="mid", fill_price=2.00,
               time_to_fill=None, exit_reason="take_profit", pnl=10.0):
        from app.api.models import DBTradeJournal
        now = datetime(2026, 6, 1, 10, 0, tzinfo=ET)
        return DBTradeJournal(
            session_date="2026-06-01",
            strategy_id="orb",
            underlying_symbol="SPY",
            option_symbol=f"SPY260601C{idx:08d}",
            status=status,
            fill_price=fill_price if status == "closed" else None,
            limit_price=1.95,
            limit_price_mode=mode,
            exit_price=2.50 if status == "closed" else None,
            realized_pnl=pnl if status == "closed" else None,
            exit_reason=exit_reason if status == "closed" else (
                "stale_cancelled" if status == "cancelled" else None
            ),
            time_to_fill_secs=time_to_fill if status == "closed" else None,
            quantity=1,
            filled_quantity=1 if status == "closed" else 0,
            entry_time=now,
            exit_time=now + timedelta(minutes=30) if status == "closed" else None,
            is_paper=True,
        )

    @pytest.mark.asyncio
    async def test_fill_rate_by_mode_single_mode(self):
        trades = [
            self._trade(1, mode="marketable_limit", status="closed"),
            self._trade(2, mode="marketable_limit", status="closed"),
            self._trade(3, mode="marketable_limit", status="cancelled"),
        ]
        report = await self._make_report(trades)
        assert "marketable_limit" in report.fill_rate_by_mode
        assert report.fill_rate_by_mode["marketable_limit"] == pytest.approx(2 / 3, abs=0.001)

    @pytest.mark.asyncio
    async def test_cancel_rate_by_mode(self):
        trades = [
            self._trade(1, mode="mid", status="closed"),
            self._trade(2, mode="mid", status="cancelled"),
            self._trade(3, mode="mid", status="cancelled"),
        ]
        report = await self._make_report(trades)
        assert report.cancel_rate_by_mode["mid"] == pytest.approx(2 / 3, abs=0.001)

    @pytest.mark.asyncio
    async def test_avg_fill_latency_by_mode(self):
        trades = [
            self._trade(1, mode="ask", status="closed", time_to_fill=30.0),
            self._trade(2, mode="ask", status="closed", time_to_fill=60.0),
            self._trade(3, mode="mid", status="closed", time_to_fill=120.0),
        ]
        report = await self._make_report(trades)
        assert "ask" in report.avg_fill_latency_by_mode
        assert report.avg_fill_latency_by_mode["ask"] == pytest.approx(45.0, abs=0.1)
        assert report.avg_fill_latency_by_mode["mid"] == pytest.approx(120.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_fill_rate_by_mode_empty_when_no_trades(self):
        report = await self._make_report([])
        assert report.fill_rate_by_mode == {}
        assert report.cancel_rate_by_mode == {}

    @pytest.mark.asyncio
    async def test_cancel_reason_breakdown_counts_reasons(self):
        trades = [
            self._trade(1, status="cancelled"),
            self._trade(2, status="cancelled"),
            self._trade(3, status="closed"),
        ]
        report = await self._make_report(trades)
        # Both cancelled trades have exit_reason="stale_cancelled"
        assert report.cancel_reason_breakdown.get("stale_cancelled", 0) == 2

    @pytest.mark.asyncio
    async def test_cancel_reason_breakdown_empty_when_no_cancels(self):
        trades = [self._trade(1, status="closed")]
        report = await self._make_report(trades)
        assert report.cancel_reason_breakdown == {}

    @pytest.mark.asyncio
    async def test_multiple_modes_tracked_independently(self):
        trades = [
            self._trade(1, mode="bid", status="cancelled"),
            self._trade(2, mode="ask", status="closed"),
            self._trade(3, mode="marketable_limit", status="closed"),
            self._trade(4, mode="marketable_limit", status="closed"),
        ]
        report = await self._make_report(trades)
        assert report.fill_rate_by_mode.get("bid", 0) == pytest.approx(0.0)
        assert report.fill_rate_by_mode.get("ask", 0) == pytest.approx(1.0)
        assert report.fill_rate_by_mode.get("marketable_limit", 0) == pytest.approx(1.0)


# ── TradeJournal: limit_price_mode stored in record_entry ────────────────────

class TestTradeJournalLimitPriceMode:

    @pytest.mark.asyncio
    async def test_record_entry_stores_limit_price_mode(self):
        from app.api.models import DBTradeJournal
        from app.trading.trade_journal import TradeJournal

        factory, engine = await _make_db()
        async with factory() as session:
            journal = TradeJournal(session, is_paper=True)
            jid = await journal.record_entry(
                entry_time=datetime(2026, 6, 1, 9, 45, tzinfo=ET),
                strategy_id="orb",
                signal_direction="LONG",
                underlying_symbol="SPY",
                underlying_price=540.0,
                option_symbol="SPY260601C00540000",
                expiration="2026-06-01",
                strike=540.0,
                option_type="call",
                delta=0.40,
                iv=0.25,
                bid=1.00,
                ask=1.20,
                spread_pct=0.18,
                limit_price=1.21,
                limit_price_mode="marketable_limit",
                quantity=1,
                order_id="test-order-abc",
            )
            await session.commit()

            row = await session.get(DBTradeJournal, jid)
            assert row.limit_price_mode == "marketable_limit"
            assert row.limit_price == pytest.approx(1.21)

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_record_entry_mode_none_when_not_provided(self):
        from app.api.models import DBTradeJournal
        from app.trading.trade_journal import TradeJournal

        factory, engine = await _make_db()
        async with factory() as session:
            journal = TradeJournal(session, is_paper=True)
            jid = await journal.record_entry(
                entry_time=datetime(2026, 6, 1, 9, 45, tzinfo=ET),
                strategy_id="orb",
                signal_direction="LONG",
                underlying_symbol="SPY",
                underlying_price=540.0,
                option_symbol="SPY260601C00540001",
                expiration="2026-06-01",
                strike=540.0,
                option_type="call",
                delta=0.40,
                iv=0.25,
                limit_price=1.10,
                quantity=1,
            )
            await session.commit()

            row = await session.get(DBTradeJournal, jid)
            assert row.limit_price_mode is None

        await engine.dispose()
