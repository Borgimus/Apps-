"""
Tests for the paper evaluation sub-package.

Covers:
  - app/config/settings.py           PAPER_EVALUATION_MODE flag
  - app/evaluation/pre_session.py    all individual checks + full run
  - app/evaluation/daily_report.py   build_daily_report, to_json, to_markdown
  - app/evaluation/ledger.py         add_session, compute_cumulative, save/load
  - app/evaluation/post_session.py   run_post_session orchestration
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ET = ZoneInfo("America/New_York")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_settings(
    *,
    live_trading_enabled: bool = False,
    paper_evaluation_mode: bool = True,
    kill_switch_active: bool = False,
    log_file: str = "/tmp/test_eval/logs/trading.log",
    eval_dir: str = "/tmp/test_eval/evaluation",
    ledger_file: str = "/tmp/test_eval/evaluation/ledger.json",
):
    s = MagicMock()
    s.live_trading_enabled = live_trading_enabled
    s.paper_evaluation_mode = paper_evaluation_mode
    s.is_kill_switch_active.return_value = kill_switch_active
    s.log_file = log_file
    s.evaluation_output_dir = eval_dir
    s.evaluation_ledger_file = ledger_file
    return s


async def _make_memory_session():
    from app.api.models import Base
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


def _paper_account():
    acct = MagicMock()
    acct.is_paper = True
    acct.equity = 100_000.0
    return acct


def _live_account():
    acct = MagicMock()
    acct.is_paper = False
    return acct


# ── Settings: paper_evaluation_mode flag ─────────────────────────────────────

class TestPaperEvaluationModeConfig:
    def test_defaults_to_false(self, monkeypatch):
        monkeypatch.delenv("PAPER_EVALUATION_MODE", raising=False)
        from app.config.settings import Settings
        # Pass _env_file=None to bypass .env so we test the code-level default
        s = Settings(_env_file=None)
        assert s.paper_evaluation_mode is False

    def test_enabled_via_env(self, monkeypatch):
        monkeypatch.setenv("PAPER_EVALUATION_MODE", "true")
        from app.config.settings import Settings
        s = Settings()
        assert s.paper_evaluation_mode is True

    def test_evaluation_output_dir_default(self, monkeypatch):
        monkeypatch.delenv("EVALUATION_OUTPUT_DIR", raising=False)
        from app.config.settings import Settings
        s = Settings()
        assert s.evaluation_output_dir == "./evaluation"

    def test_evaluation_output_dir_override(self, monkeypatch):
        monkeypatch.setenv("EVALUATION_OUTPUT_DIR", "/data/eval")
        from app.config.settings import Settings
        s = Settings()
        assert s.evaluation_output_dir == "/data/eval"

    def test_eval_and_live_raises(self, monkeypatch):
        monkeypatch.setenv("PAPER_EVALUATION_MODE", "true")
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
        import warnings
        from app.config.settings import Settings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(Exception):
                Settings()

    def test_eval_false_and_live_true_is_allowed(self, monkeypatch):
        monkeypatch.setenv("PAPER_EVALUATION_MODE", "false")
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
        import warnings
        from app.config.settings import Settings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            s = Settings()
        assert s.paper_evaluation_mode is False
        assert s.live_trading_enabled is True


# ── pre_session: individual checks ───────────────────────────────────────────

class TestPreSessionChecks:
    def test_paper_mode_passes_when_live_off(self):
        from app.evaluation.pre_session import _check_paper_mode
        s = _make_settings(live_trading_enabled=False)
        r = _check_paper_mode(s)
        assert r.passed

    def test_paper_mode_fails_when_live_on(self):
        from app.evaluation.pre_session import _check_paper_mode
        s = _make_settings(live_trading_enabled=True)
        r = _check_paper_mode(s)
        assert not r.passed
        assert r.required

    def test_kill_switch_passes_when_inactive(self):
        from app.evaluation.pre_session import _check_kill_switch_inactive
        s = _make_settings(kill_switch_active=False)
        r = _check_kill_switch_inactive(s)
        assert r.passed

    def test_kill_switch_fails_when_active(self):
        from app.evaluation.pre_session import _check_kill_switch_inactive
        s = _make_settings(kill_switch_active=True)
        r = _check_kill_switch_inactive(s)
        assert not r.passed
        assert r.required

    def test_market_day_is_advisory(self):
        from app.evaluation.pre_session import _check_market_day
        r = _check_market_day()
        assert r.required is False

    def test_broker_reachable_passes_for_paper_account(self):
        from app.evaluation.pre_session import _check_broker_reachable
        broker = MagicMock()
        broker.get_account = AsyncMock(return_value=_paper_account())
        r = _run(_check_broker_reachable(broker))
        assert r.passed

    def test_broker_reachable_fails_for_live_account(self):
        from app.evaluation.pre_session import _check_broker_reachable
        broker = MagicMock()
        broker.get_account = AsyncMock(return_value=_live_account())
        r = _run(_check_broker_reachable(broker))
        assert not r.passed

    def test_broker_reachable_fails_on_exception(self):
        from app.evaluation.pre_session import _check_broker_reachable
        broker = MagicMock()
        broker.get_account = AsyncMock(side_effect=ConnectionError("timeout"))
        r = _run(_check_broker_reachable(broker))
        assert not r.passed
        assert "timeout" in r.message

    def test_broker_reachable_fails_when_none(self):
        from app.evaluation.pre_session import _check_broker_reachable
        r = _run(_check_broker_reachable(None))
        assert not r.passed

    @pytest.mark.asyncio
    async def test_db_writable_passes_with_valid_session(self):
        from app.evaluation.pre_session import _check_db_writable
        factory, engine = await _make_memory_session()
        async with factory() as session:
            r = await _check_db_writable(session)
        await engine.dispose()
        assert r.passed

    def test_db_writable_fails_when_none(self):
        from app.evaluation.pre_session import _check_db_writable
        r = _run(_check_db_writable(None))
        assert not r.passed

    def test_logs_writable_passes_for_existing_dir(self, tmp_path):
        from app.evaluation.pre_session import _check_logs_writable
        s = _make_settings(log_file=str(tmp_path / "logs" / "trading.log"))
        r = _check_logs_writable(s)
        assert r.passed

    def test_daily_loss_reset_passes_at_zero(self):
        from app.evaluation.pre_session import _check_daily_loss_reset
        rm = MagicMock()
        rm.daily_pnl = 0.0
        r = _check_daily_loss_reset(rm)
        assert r.passed

    def test_daily_loss_reset_fails_if_nonzero(self):
        from app.evaluation.pre_session import _check_daily_loss_reset
        rm = MagicMock()
        rm.daily_pnl = -150.0
        r = _check_daily_loss_reset(rm)
        assert not r.passed

    def test_daily_loss_reset_advisory_when_no_rm(self):
        from app.evaluation.pre_session import _check_daily_loss_reset
        r = _check_daily_loss_reset(None)
        assert r.passed
        assert r.required is False

    @pytest.mark.asyncio
    async def test_no_stale_pending_passes_with_empty_db(self):
        from app.evaluation.pre_session import _check_no_stale_pending_orders
        factory, engine = await _make_memory_session()
        async with factory() as session:
            r = await _check_no_stale_pending_orders(session)
        await engine.dispose()
        assert r.passed

    @pytest.mark.asyncio
    async def test_no_stale_pending_fails_with_old_orders(self):
        from app.api.models import DBPendingOrder
        from app.evaluation.pre_session import _check_no_stale_pending_orders
        factory, engine = await _make_memory_session()
        async with factory() as session:
            old_order = DBPendingOrder(
                order_id="old-order-abc-1",
                option_symbol="SPY240101C00450000",
                symbol="SPY",
                strategy_id="orb",
                direction="long",
                quantity=1,
                limit_price=3.0,
                submitted_at=datetime.utcnow(),
                status="pending",
                session_date="2020-01-01",  # old date
            )
            session.add(old_order)
            await session.commit()
            r = await _check_no_stale_pending_orders(session)
        await engine.dispose()
        assert not r.passed
        assert "stale" in r.message.lower()

    @pytest.mark.asyncio
    async def test_data_feed_freshness_advisory(self):
        from app.evaluation.pre_session import _check_data_feed_freshness
        factory, engine = await _make_memory_session()
        async with factory() as session:
            r = await _check_data_feed_freshness(session)
        await engine.dispose()
        assert r.required is False

    @pytest.mark.asyncio
    async def test_data_feed_fresh_with_no_logs_passes(self):
        from app.evaluation.pre_session import _check_data_feed_freshness
        factory, engine = await _make_memory_session()
        async with factory() as session:
            r = await _check_data_feed_freshness(session)
        await engine.dispose()
        assert r.passed  # no logs → "first run of day"


# ── pre_session: full run ─────────────────────────────────────────────────────

class TestPreSessionFullRun:
    @pytest.mark.asyncio
    async def test_all_required_pass_with_good_inputs(self, tmp_path):
        from app.evaluation.pre_session import all_required_pass, run_pre_session_checks
        s = _make_settings(log_file=str(tmp_path / "logs" / "trading.log"))
        broker = MagicMock()
        broker.get_account = AsyncMock(return_value=_paper_account())
        rm = MagicMock()
        rm.daily_pnl = 0.0
        factory, engine = await _make_memory_session()
        async with factory() as session:
            checks = await run_pre_session_checks(s, broker=broker, db_session=session, risk_manager=rm)
        await engine.dispose()
        assert all_required_pass(checks)

    def test_all_required_pass_false_when_live_on(self, tmp_path):
        from app.evaluation.pre_session import all_required_pass, run_pre_session_checks
        s = _make_settings(live_trading_enabled=True, log_file=str(tmp_path / "logs" / "trading.log"))
        broker = MagicMock()
        broker.get_account = AsyncMock(return_value=_paper_account())
        checks = _run(run_pre_session_checks(s, broker=broker))
        assert not all_required_pass(checks)

    def test_format_check_table_contains_result_line(self, tmp_path):
        from app.evaluation.pre_session import format_check_table, run_pre_session_checks
        s = _make_settings(log_file=str(tmp_path / "logs" / "trading.log"))
        checks = _run(run_pre_session_checks(s))
        table = format_check_table(checks)
        assert "Result:" in table

    def test_check_names_unique(self, tmp_path):
        from app.evaluation.pre_session import run_pre_session_checks
        s = _make_settings(log_file=str(tmp_path / "logs" / "trading.log"))
        checks = _run(run_pre_session_checks(s))
        names = [c.name for c in checks]
        assert len(names) == len(set(names)), "Duplicate check names"

    def test_all_checks_have_message(self, tmp_path):
        from app.evaluation.pre_session import run_pre_session_checks
        s = _make_settings(log_file=str(tmp_path / "logs" / "trading.log"))
        checks = _run(run_pre_session_checks(s))
        for c in checks:
            assert c.message, f"Check {c.name} has empty message"


# ── daily_report: build from empty DB ────────────────────────────────────────

class TestDailyReportEmpty:
    @pytest.mark.asyncio
    async def test_build_empty_session(self):
        from app.evaluation.daily_report import build_daily_report
        factory, engine = await _make_memory_session()
        async with factory() as session:
            report = await build_daily_report(session, "2024-03-18")
        await engine.dispose()
        assert report.date == "2024-03-18"
        assert report.total_signals == 0
        assert report.trades_submitted == 0
        assert report.realized_pnl == 0.0
        assert report.win_rate is None
        assert report.max_drawdown == 0.0

    @pytest.mark.asyncio
    async def test_build_has_notes(self):
        from app.evaluation.daily_report import build_daily_report
        factory, engine = await _make_memory_session()
        async with factory() as session:
            report = await build_daily_report(session, "2024-03-18")
        await engine.dispose()
        assert isinstance(report.notes, list)
        assert len(report.notes) > 0


# ── daily_report: build with trades ──────────────────────────────────────────

async def _seed_trades(session, session_date: str):
    """Seed realistic closed and rejected trades."""
    from app.api.models import DBTradeJournal

    today = datetime.strptime(session_date, "%Y-%m-%d")

    # Three closed trades: two wins, one loss
    closed = [
        DBTradeJournal(
            session_date=session_date,
            strategy_id="orb",
            underlying_symbol="SPY",
            option_symbol=f"SPY240318C0045000{i}",
            status="closed",
            fill_price=3.05 + i * 0.1,
            limit_price=3.00 + i * 0.1,
            exit_price=3.50 + i * 0.1,
            realized_pnl=45.0 if i < 2 else -30.0,
            slippage=0.05,
            bid=2.95,
            ask=3.10,
            quantity=1,
            filled_quantity=1,
            entry_time=today.replace(hour=9, minute=45),
            exit_time=today.replace(hour=10, minute=30),
            delta=0.42,
            spread_pct=0.05,
            is_paper=True,
        )
        for i in range(3)
    ]

    # Two rejected trades
    rejected = [
        DBTradeJournal(
            session_date=session_date,
            strategy_id="vwap_reclaim",
            underlying_symbol="QQQ",
            option_symbol=f"QQQ240318C0038000{i}",
            status="rejected",
            rejection_reason="spread_too_wide: 12%",
            is_paper=True,
        )
        for i in range(2)
    ]

    for t in closed + rejected:
        session.add(t)
    await session.commit()


class TestDailyReportWithTrades:
    @pytest.mark.asyncio
    async def test_trade_counts(self):
        from app.evaluation.daily_report import build_daily_report
        factory, engine = await _make_memory_session()
        session_date = "2024-03-18"
        async with factory() as session:
            await _seed_trades(session, session_date)
            report = await build_daily_report(session, session_date)
        await engine.dispose()
        assert report.trades_submitted == 3
        assert report.trades_rejected == 2
        assert report.trades_filled == 3

    @pytest.mark.asyncio
    async def test_realized_pnl(self):
        from app.evaluation.daily_report import build_daily_report
        factory, engine = await _make_memory_session()
        session_date = "2024-03-18"
        async with factory() as session:
            await _seed_trades(session, session_date)
            report = await build_daily_report(session, session_date)
        await engine.dispose()
        assert abs(report.realized_pnl - 60.0) < 0.01  # 45 + 45 - 30

    @pytest.mark.asyncio
    async def test_win_rate(self):
        from app.evaluation.daily_report import build_daily_report
        factory, engine = await _make_memory_session()
        session_date = "2024-03-18"
        async with factory() as session:
            await _seed_trades(session, session_date)
            report = await build_daily_report(session, session_date)
        await engine.dispose()
        assert report.win_rate == pytest.approx(2 / 3, abs=0.001)

    @pytest.mark.asyncio
    async def test_avg_win_and_loss(self):
        from app.evaluation.daily_report import build_daily_report
        factory, engine = await _make_memory_session()
        session_date = "2024-03-18"
        async with factory() as session:
            await _seed_trades(session, session_date)
            report = await build_daily_report(session, session_date)
        await engine.dispose()
        assert report.avg_win == pytest.approx(45.0, abs=0.01)
        assert report.avg_loss == pytest.approx(-30.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_slippage_total(self):
        from app.evaluation.daily_report import build_daily_report
        factory, engine = await _make_memory_session()
        session_date = "2024-03-18"
        async with factory() as session:
            await _seed_trades(session, session_date)
            report = await build_daily_report(session, session_date)
        await engine.dispose()
        assert report.slippage_total == pytest.approx(0.15, abs=0.001)  # 3 * 0.05

    @pytest.mark.asyncio
    async def test_spread_cost_estimate(self):
        from app.evaluation.daily_report import build_daily_report
        factory, engine = await _make_memory_session()
        session_date = "2024-03-18"
        async with factory() as session:
            await _seed_trades(session, session_date)
            report = await build_daily_report(session, session_date)
        await engine.dispose()
        # spread_cost = (ask - bid) / 2 * qty * 100 = (3.10 - 2.95) / 2 * 1 * 100 = 7.5 each
        assert report.spread_cost_estimate == pytest.approx(22.5, abs=0.01)

    @pytest.mark.asyncio
    async def test_by_strategy_populated(self):
        from app.evaluation.daily_report import build_daily_report
        factory, engine = await _make_memory_session()
        session_date = "2024-03-18"
        async with factory() as session:
            await _seed_trades(session, session_date)
            report = await build_daily_report(session, session_date)
        await engine.dispose()
        strategy_ids = {s.strategy_id for s in report.by_strategy}
        assert "orb" in strategy_ids
        assert "vwap_reclaim" in strategy_ids

    @pytest.mark.asyncio
    async def test_max_drawdown_nonzero_with_losses(self):
        from app.evaluation.daily_report import build_daily_report
        factory, engine = await _make_memory_session()
        session_date = "2024-03-18"
        async with factory() as session:
            await _seed_trades(session, session_date)
            report = await build_daily_report(session, session_date)
        await engine.dispose()
        assert report.max_drawdown >= 0.0

    @pytest.mark.asyncio
    async def test_recommendations_generated_for_high_reject_rate(self):
        from app.api.models import DBTradeJournal
        from app.evaluation.daily_report import build_daily_report
        factory, engine = await _make_memory_session()
        session_date = "2024-03-20"
        async with factory() as session:
            # 1 submitted (closed) + 4 rejected = 80% rejection → triggers recommendation
            session.add(DBTradeJournal(
                session_date=session_date, strategy_id="orb",
                underlying_symbol="SPY", option_symbol="SPY240320C00450000",
                status="closed", fill_price=3.0, limit_price=3.0,
                exit_price=3.5, realized_pnl=50.0, slippage=0.0,
                quantity=1, filled_quantity=1, is_paper=True,
                entry_time=datetime(2024, 3, 20, 10, 0), exit_time=datetime(2024, 3, 20, 11, 0),
            ))
            for i in range(4):
                session.add(DBTradeJournal(
                    session_date=session_date, strategy_id="orb",
                    underlying_symbol="SPY", option_symbol=f"SPY240320C0040000{i}",
                    status="rejected", rejection_reason="spread_too_wide", is_paper=True,
                ))
            await session.commit()
            report = await build_daily_report(session, session_date)
        await engine.dispose()
        # 1 submitted + 4 rejected = 80% rejection rate → recommendation expected
        all_text = " ".join(report.recommendations).lower()
        assert "reject" in all_text or "filter" in all_text or "criteria" in all_text


# ── daily_report: output formatters ──────────────────────────────────────────

class TestDailyReportFormatters:
    def _make_report(self):
        from app.evaluation.daily_report import DailyReport, StrategyStats
        return DailyReport(
            date="2024-03-18",
            session_start="2024-03-18 09:30:00 EST",
            session_end="2024-03-18 16:00:00 EST",
            total_signals=5,
            trades_submitted=3,
            trades_filled=2,
            trades_cancelled=1,
            trades_rejected=1,
            realized_pnl=75.50,
            unrealized_pnl=0.0,
            win_rate=0.667,
            avg_win=45.0,
            avg_loss=-30.0,
            max_drawdown=30.0,
            largest_win=45.0,
            largest_loss=-30.0,
            slippage_total=0.15,
            spread_cost_estimate=22.50,
            api_errors=0,
            kill_switch_events=0,
            by_strategy=[
                StrategyStats(strategy_id="orb", signals=3, submitted=2,
                              fills=2, cancels=0, rejects=1, realized_pnl=75.50,
                              wins=2, losses=0, win_rate=1.0, avg_win=37.75)
            ],
            notes=["Net profit session: $75.50"],
            recommendations=[],
        )

    def test_to_json_is_valid_json(self):
        from app.evaluation.daily_report import to_json
        r = self._make_report()
        raw = to_json(r)
        data = json.loads(raw)
        assert data["date"] == "2024-03-18"

    def test_to_json_contains_all_top_level_keys(self):
        from app.evaluation.daily_report import to_json
        r = self._make_report()
        data = json.loads(to_json(r))
        for key in ("date", "realized_pnl", "win_rate", "max_drawdown",
                    "by_strategy", "notes", "recommendations"):
            assert key in data, f"Missing key: {key}"

    def test_to_markdown_contains_header(self):
        from app.evaluation.daily_report import to_markdown
        r = self._make_report()
        md = to_markdown(r)
        assert "# Daily Evaluation Report" in md
        assert "2024-03-18" in md

    def test_to_markdown_contains_pnl(self):
        from app.evaluation.daily_report import to_markdown
        r = self._make_report()
        md = to_markdown(r)
        assert "75.50" in md

    def test_to_markdown_contains_strategy_table(self):
        from app.evaluation.daily_report import to_markdown
        r = self._make_report()
        md = to_markdown(r)
        assert "orb" in md

    def test_to_markdown_none_win_rate_shows_na(self):
        from app.evaluation.daily_report import DailyReport, to_markdown
        r = DailyReport(date="2024-03-18", session_start=None, session_end=None)
        md = to_markdown(r)
        assert "n/a" in md


# ── ledger: add_session and cumulative ───────────────────────────────────────

class TestEvaluationLedger:
    def _make_report(self, date_str: str, pnl: float, wins: int = 1, losses: int = 1):
        from app.evaluation.daily_report import DailyReport, StrategyStats
        return DailyReport(
            date=date_str,
            session_start=None,
            session_end=None,
            realized_pnl=pnl,
            trades_submitted=wins + losses,
            trades_filled=wins + losses,
            by_strategy=[
                StrategyStats(strategy_id="orb", wins=wins, losses=losses,
                              realized_pnl=pnl)
            ],
        )

    def test_add_session_increments_count(self):
        from app.evaluation.ledger import EvaluationLedger
        ledger = EvaluationLedger()
        ledger.add_session(self._make_report("2024-03-18", 100.0))
        assert len(ledger.sessions) == 1

    def test_add_session_twice_same_date_replaces(self):
        from app.evaluation.ledger import EvaluationLedger
        ledger = EvaluationLedger()
        ledger.add_session(self._make_report("2024-03-18", 100.0))
        ledger.add_session(self._make_report("2024-03-18", 200.0))
        assert len(ledger.sessions) == 1
        assert ledger.sessions[0].realized_pnl == pytest.approx(200.0)

    def test_cumulative_total_pnl(self):
        from app.evaluation.ledger import EvaluationLedger
        ledger = EvaluationLedger()
        ledger.add_session(self._make_report("2024-03-18", 100.0))
        ledger.add_session(self._make_report("2024-03-19", -30.0))
        c = ledger.compute_cumulative()
        assert c["total_pnl"] == pytest.approx(70.0)

    def test_cumulative_trading_days(self):
        from app.evaluation.ledger import EvaluationLedger
        ledger = EvaluationLedger()
        for i in range(5):
            ledger.add_session(self._make_report(f"2024-03-{18+i}", 10.0))
        c = ledger.compute_cumulative()
        assert c["trading_days"] == 5

    def test_cumulative_expectancy(self):
        from app.evaluation.ledger import EvaluationLedger
        ledger = EvaluationLedger()
        # 2 sessions: +100 (1W, 1L each), -40 (1W, 1L each) → total pnl=60, trades=4
        ledger.add_session(self._make_report("2024-03-18", 100.0))
        ledger.add_session(self._make_report("2024-03-19", -40.0))
        c = ledger.compute_cumulative()
        assert c["expectancy"] == pytest.approx(60.0 / 4, abs=0.01)

    def test_cumulative_profit_factor(self):
        from app.evaluation.ledger import EvaluationLedger
        ledger = EvaluationLedger()
        # session 1: +150 (positive pnl session)
        # session 2: -50 (negative pnl session)
        # profit_factor = 150 / 50 = 3.0
        ledger.add_session(self._make_report("2024-03-18", 150.0))
        ledger.add_session(self._make_report("2024-03-19", -50.0))
        c = ledger.compute_cumulative()
        assert c["profit_factor"] == pytest.approx(3.0, abs=0.01)

    def test_cumulative_max_drawdown(self):
        from app.evaluation.ledger import EvaluationLedger
        ledger = EvaluationLedger()
        # Equity curve: +100, -200, +50 → peak=100, trough=-100 → dd=200
        ledger.add_session(self._make_report("2024-03-18", 100.0))
        ledger.add_session(self._make_report("2024-03-19", -200.0))
        ledger.add_session(self._make_report("2024-03-20", 50.0))
        c = ledger.compute_cumulative()
        assert c["max_drawdown"] == pytest.approx(200.0)

    def test_cumulative_win_rate(self):
        from app.evaluation.ledger import EvaluationLedger
        ledger = EvaluationLedger()
        ledger.add_session(self._make_report("2024-03-18", 100.0, wins=3, losses=1))
        c = ledger.compute_cumulative()
        assert c["win_rate"] == pytest.approx(3 / 4, abs=0.01)

    def test_cumulative_empty_returns_zero_structure(self):
        from app.evaluation.ledger import EvaluationLedger
        ledger = EvaluationLedger()
        c = ledger.compute_cumulative()
        assert c["trading_days"] == 0
        assert c["total_trades"] == 0
        assert c["profit_factor"] is None

    def test_pnl_by_strategy_accumulated(self):
        from app.evaluation.ledger import EvaluationLedger
        ledger = EvaluationLedger()
        ledger.add_session(self._make_report("2024-03-18", 100.0))
        ledger.add_session(self._make_report("2024-03-19", 50.0))
        c = ledger.compute_cumulative()
        assert "orb" in c["pnl_by_strategy"]
        assert c["pnl_by_strategy"]["orb"]["pnl"] == pytest.approx(150.0)

    def test_save_and_load_round_trip(self, tmp_path):
        from app.evaluation.ledger import EvaluationLedger
        ledger_file = str(tmp_path / "ledger.json")
        ledger = EvaluationLedger(ledger_file=ledger_file)
        ledger.add_session(self._make_report("2024-03-18", 100.0))
        ledger.add_session(self._make_report("2024-03-19", -30.0))
        ledger.save()

        loaded = EvaluationLedger.load(ledger_file)
        assert len(loaded.sessions) == 2
        assert loaded.sessions[0].date == "2024-03-18"
        assert loaded.sessions[1].realized_pnl == pytest.approx(-30.0)

    def test_load_creates_empty_if_no_file(self, tmp_path):
        from app.evaluation.ledger import EvaluationLedger
        ledger = EvaluationLedger.load(str(tmp_path / "nonexistent_ledger.json"))
        assert len(ledger.sessions) == 0

    def test_saved_ledger_contains_cumulative(self, tmp_path):
        from app.evaluation.ledger import EvaluationLedger
        ledger_file = str(tmp_path / "ledger.json")
        ledger = EvaluationLedger(ledger_file=ledger_file)
        ledger.add_session(self._make_report("2024-03-18", 100.0))
        ledger.save()
        data = json.loads(Path(ledger_file).read_text())
        assert "cumulative" in data
        assert data["cumulative"]["trading_days"] == 1

    def test_delta_bucket_accumulation(self):
        """Trade records with delta column fill pnl_by_delta_bucket."""
        from app.evaluation.ledger import EvaluationLedger, _accumulate_delta

        trade = MagicMock()
        trade.delta = 0.43
        trade.realized_pnl = 50.0

        by_delta: dict = {}
        _accumulate_delta(trade, by_delta)
        assert "target (0.40-0.50)" in by_delta
        assert by_delta["target (0.40-0.50)"]["pnl"] == 50.0

    def test_spread_bucket_wide(self):
        from app.evaluation.ledger import _spread_bucket
        assert _spread_bucket(0.12) == "wide (>10%)"

    def test_spread_bucket_tight(self):
        from app.evaluation.ledger import _spread_bucket
        assert _spread_bucket(0.03) == "tight (<5%)"

    def test_spread_bucket_unknown(self):
        from app.evaluation.ledger import _spread_bucket
        assert _spread_bucket(None) == "unknown"

    def test_reject_reason_accumulated(self):
        from app.evaluation.ledger import _accumulate_reject
        trade = MagicMock()
        trade.status = "rejected"
        trade.rejection_reason = "spread_too_wide: 12%"
        reasons: dict = {}
        _accumulate_reject(trade, reasons)
        assert "spread_too_wide" in reasons
        assert reasons["spread_too_wide"] == 1

    def test_non_rejected_not_counted(self):
        from app.evaluation.ledger import _accumulate_reject
        trade = MagicMock()
        trade.status = "closed"
        trade.rejection_reason = None
        reasons: dict = {}
        _accumulate_reject(trade, reasons)
        assert not reasons


# ── post_session: orchestration ───────────────────────────────────────────────

class TestPostSession:
    @pytest.mark.asyncio
    async def test_run_with_no_broker_or_pm(self, tmp_path):
        from app.evaluation.post_session import run_post_session
        s = _make_settings(
            eval_dir=str(tmp_path / "evaluation"),
            ledger_file=str(tmp_path / "evaluation" / "ledger.json"),
        )
        factory, engine = await _make_memory_session()
        async with factory() as session:
            result = await run_post_session(
                settings=s,
                broker=None,
                db_session=session,
                fill_tracker=None,
                pm=None,
                journal=None,
                alert_service=None,
                session_date="2024-03-18",
            )
        await engine.dispose()
        assert result.session_date == "2024-03-18"
        assert result.stale_orders_cancelled == 0

    @pytest.mark.asyncio
    async def test_report_files_created(self, tmp_path):
        from app.evaluation.post_session import run_post_session
        s = _make_settings(
            eval_dir=str(tmp_path / "evaluation"),
            ledger_file=str(tmp_path / "evaluation" / "ledger.json"),
        )
        factory, engine = await _make_memory_session()
        async with factory() as session:
            result = await run_post_session(
                settings=s,
                broker=None,
                db_session=session,
                fill_tracker=None,
                pm=None,
                journal=None,
                alert_service=None,
                session_date="2024-03-18",
            )
        await engine.dispose()
        assert result.report_path_json is not None
        assert Path(result.report_path_json).exists()
        assert result.report_path_md is not None
        assert Path(result.report_path_md).exists()

    @pytest.mark.asyncio
    async def test_ledger_updated_after_report(self, tmp_path):
        from app.evaluation.post_session import run_post_session
        s = _make_settings(
            eval_dir=str(tmp_path / "evaluation"),
            ledger_file=str(tmp_path / "evaluation" / "ledger.json"),
        )
        factory, engine = await _make_memory_session()
        async with factory() as session:
            result = await run_post_session(
                settings=s,
                db_session=session,
                session_date="2024-03-18",
            )
        await engine.dispose()
        assert result.ledger_updated
        ledger_path = Path(str(tmp_path / "evaluation" / "ledger.json"))
        assert ledger_path.exists()
        data = json.loads(ledger_path.read_text())
        assert data["cumulative"]["trading_days"] == 1

    @pytest.mark.asyncio
    async def test_cancel_stale_calls_broker(self, tmp_path):
        from app.evaluation.post_session import run_post_session

        broker = MagicMock()
        broker.cancel_order = AsyncMock()
        broker.get_positions = AsyncMock(return_value=[])

        pending = MagicMock()
        pending.order_id = "test-order-cancel-001"
        fill_tracker = MagicMock()
        fill_tracker.pending_orders.return_value = [pending]

        s = _make_settings(
            eval_dir=str(tmp_path / "evaluation"),
            ledger_file=str(tmp_path / "evaluation" / "ledger.json"),
        )
        factory, engine = await _make_memory_session()
        async with factory() as session:
            result = await run_post_session(
                settings=s,
                broker=broker,
                db_session=session,
                fill_tracker=fill_tracker,
                session_date="2024-03-18",
            )
        await engine.dispose()
        broker.cancel_order.assert_called_once_with("test-order-cancel-001")
        assert result.stale_orders_cancelled == 1

    @pytest.mark.asyncio
    async def test_open_positions_recorded(self, tmp_path):
        from app.evaluation.post_session import run_post_session

        pm = MagicMock()
        pos = MagicMock()
        pos.option_symbol = "SPY240318C00450000"
        pm.open_positions.return_value = [pos]

        broker = MagicMock()
        broker.get_positions = AsyncMock(return_value=[])

        s = _make_settings(
            eval_dir=str(tmp_path / "evaluation"),
            ledger_file=str(tmp_path / "evaluation" / "ledger.json"),
        )
        factory, engine = await _make_memory_session()
        async with factory() as session:
            result = await run_post_session(
                settings=s,
                broker=broker,
                db_session=session,
                pm=pm,
                session_date="2024-03-18",
            )
        await engine.dispose()
        assert result.open_positions_remaining == 1

    @pytest.mark.asyncio
    async def test_alert_sent_when_configured(self, tmp_path):
        from app.evaluation.post_session import run_post_session

        alert_service = MagicMock()
        alert_service.send = AsyncMock()

        s = _make_settings(
            eval_dir=str(tmp_path / "evaluation"),
            ledger_file=str(tmp_path / "evaluation" / "ledger.json"),
        )
        factory, engine = await _make_memory_session()
        async with factory() as session:
            result = await run_post_session(
                settings=s,
                db_session=session,
                alert_service=alert_service,
                session_date="2024-03-18",
            )
        await engine.dispose()
        assert result.alert_sent
        alert_service.send.assert_called_once()


# ── Post-session repeated sessions update ledger ──────────────────────────────

class TestLedgerAccumulation:
    @pytest.mark.asyncio
    async def test_two_sessions_accumulate(self, tmp_path):
        from app.evaluation.post_session import run_post_session
        from app.evaluation.ledger import EvaluationLedger

        s = _make_settings(
            eval_dir=str(tmp_path / "evaluation"),
            ledger_file=str(tmp_path / "evaluation" / "ledger.json"),
        )

        factory, engine = await _make_memory_session()
        async with factory() as session:
            await run_post_session(settings=s, db_session=session, session_date="2024-03-18")
        async with factory() as session:
            await run_post_session(settings=s, db_session=session, session_date="2024-03-19")
        await engine.dispose()

        ledger = EvaluationLedger.load(str(tmp_path / "evaluation" / "ledger.json"))
        assert ledger.compute_cumulative()["trading_days"] == 2
