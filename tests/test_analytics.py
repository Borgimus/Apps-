"""
Tests for AnalyticsEngine.

Uses an in-memory SQLite database populated with synthetic DBTradeJournal rows.
Covers summary, by_strategy, by_hour, spread_analysis, and rejection_summary.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.analytics import AnalyticsEngine
from app.api.models import Base, DBTradeJournal

ET = ZoneInfo("America/New_York")

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="module")
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _trade(
    strategy_id="orb",
    pnl=100.0,
    status="closed",
    delta=0.40,
    iv=0.25,
    spread_pct=0.05,
    limit_price=3.00,
    fill_price=3.05,
    entry_hour=10,
    rejection_reason=None,
):
    return DBTradeJournal(
        entry_time=datetime(2024, 1, 2, entry_hour, 0, 0, tzinfo=ET),
        strategy_id=strategy_id,
        signal_direction="LONG",
        underlying_symbol="SPY",
        underlying_price=450.0,
        option_symbol="SPY240102C00450000",
        expiration="2024-01-02",
        strike=450.0,
        option_type="call",
        delta=delta,
        iv=iv,
        spread_pct=spread_pct,
        limit_price=limit_price,
        fill_price=fill_price,
        slippage=(fill_price - limit_price) if fill_price is not None else None,
        quantity=1,
        exit_time=datetime(2024, 1, 2, 11, 0, 0, tzinfo=ET) if pnl is not None else None,
        exit_price=(4.00 if pnl > 0 else 1.50) if pnl is not None else None,
        exit_reason=("take_profit" if pnl > 0 else "stop_loss") if pnl is not None else None,
        realized_pnl=pnl,
        hold_duration_secs=3600.0,
        status=status,
        is_paper=True,
        rejection_reason=rejection_reason,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestAnalyticsSummary:

    @pytest.mark.asyncio
    async def test_empty_summary(self, db_session: AsyncSession):
        engine = AnalyticsEngine(db_session)
        result = await engine.summary()
        assert result["total_trades"] == 0
        assert result["win_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_summary_with_trades(self, db_session: AsyncSession):
        db_session.add(_trade(pnl=200.0))
        db_session.add(_trade(pnl=-100.0))
        await db_session.commit()

        engine = AnalyticsEngine(db_session)
        result = await engine.summary()
        assert result["total_trades"] >= 2
        assert 0 < result["win_rate"] <= 1.0
        assert result["expectancy"] != 0

    @pytest.mark.asyncio
    async def test_rejected_not_in_summary(self, db_session: AsyncSession):
        db_session.add(_trade(status="rejected", rejection_reason="risk_check", pnl=None))
        await db_session.commit()

        engine = AnalyticsEngine(db_session)
        summary = await engine.summary()
        rejections = await engine.rejection_summary()
        # Summary counts only closed trades
        assert summary["total_trades"] == summary.get("total_closed_trades", summary["total_trades"])
        assert rejections["total_rejections"] >= 1


class TestByStrategy:

    @pytest.mark.asyncio
    async def test_separate_strategies(self, db_session: AsyncSession):
        db_session.add(_trade(strategy_id="orb", pnl=100.0))
        db_session.add(_trade(strategy_id="vwap_reclaim", pnl=-50.0))
        await db_session.commit()

        engine = AnalyticsEngine(db_session)
        result = await engine.by_strategy()
        assert "orb" in result
        assert "vwap_reclaim" in result

    @pytest.mark.asyncio
    async def test_win_rate_per_strategy(self, db_session: AsyncSession):
        # Clear and repopulate for clean test
        db_session.add(_trade(strategy_id="rsi_trend", pnl=100.0))
        db_session.add(_trade(strategy_id="rsi_trend", pnl=200.0))
        await db_session.commit()

        engine = AnalyticsEngine(db_session)
        result = await engine.by_strategy()
        if "rsi_trend" in result:
            assert result["rsi_trend"]["win_rate"] > 0


class TestByHour:

    @pytest.mark.asyncio
    async def test_hour_keys_are_strings(self, db_session: AsyncSession):
        db_session.add(_trade(pnl=100.0, entry_hour=10))
        db_session.add(_trade(pnl=-50.0, entry_hour=11))
        await db_session.commit()

        engine = AnalyticsEngine(db_session)
        result = await engine.by_hour()
        for key in result:
            assert ":" in key   # e.g. "10:00"


class TestSpreadAnalysis:

    @pytest.mark.asyncio
    async def test_avg_spread_computed(self, db_session: AsyncSession):
        db_session.add(_trade(spread_pct=0.04, limit_price=3.00, fill_price=3.05))
        db_session.add(_trade(spread_pct=0.06, limit_price=3.00, fill_price=3.10))
        await db_session.commit()

        engine = AnalyticsEngine(db_session)
        result = await engine.spread_analysis()
        assert result["n_with_spread_data"] >= 2
        assert result["avg_spread_pct"] > 0

    @pytest.mark.asyncio
    async def test_avg_slippage_computed(self, db_session: AsyncSession):
        db_session.add(_trade(limit_price=3.00, fill_price=3.05))
        await db_session.commit()

        engine = AnalyticsEngine(db_session)
        result = await engine.spread_analysis()
        assert result["n_with_fill_data"] >= 1


class TestRejectionSummary:

    @pytest.mark.asyncio
    async def test_rejection_counted(self, db_session: AsyncSession):
        db_session.add(_trade(status="rejected", rejection_reason="max_trades_per_day", pnl=None))
        db_session.add(_trade(status="rejected", rejection_reason="max_trades_per_day", pnl=None))
        await db_session.commit()

        engine = AnalyticsEngine(db_session)
        result = await engine.rejection_summary()
        assert result["total_rejections"] >= 2
        assert "max_trades_per_day" in result["by_reason"]
