#!/usr/bin/env python3
"""
Paper evaluation smoke test.

Exercises every component of the evaluation pipeline end-to-end without
requiring live market hours or real broker orders.

Steps
-----
1.  Settings & no-live-path guard
2.  Pre-session checklist  (PaperBroker + in-memory SQLite)
3.  Seed 3 closed trades, 1 rejection, 1 pending order, session logs
4.  Pending order cancellation via post-session workflow
5.  Daily evaluation report  (JSON + Markdown, metric assertions)
6.  Evaluation ledger  (written, cumulative stats verified)
7.  Second session accumulates in ledger  (trading_days == 2)
8.  Alert service no-op when unconfigured
9.  /health endpoint reports paper_mode + paper_evaluation_mode

Exit codes
----------
  0  all checks passed
  1  one or more checks failed
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import traceback
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root))

# Force paper mode before settings are loaded
os.environ["PAPER_EVALUATION_MODE"] = "true"
os.environ["BROKER"] = "paper"
os.environ.setdefault("LIVE_TRADING_ENABLED", "false")

_PASS = "\033[92m✓\033[0m"
_FAIL = "\033[91m✗\033[0m"
_WARN = "\033[93m⚠\033[0m"

_results: list[tuple[str, bool, str]] = []


def _ok(name: str, detail: str = "") -> None:
    _results.append((name, True, detail))
    print(f"  {_PASS}  {name}" + (f"  [{detail}]" if detail else ""))


def _fail(name: str, detail: str = "") -> None:
    _results.append((name, False, detail))
    print(f"  {_FAIL}  {name}" + (f"  [{detail}]" if detail else ""))


def _section(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def _fake_settings(log_file: str, eval_dir: str, ledger_file: str):
    """MagicMock that mimics Settings with paths pointing at tmp dirs."""
    s = MagicMock()
    s.live_trading_enabled = False
    s.paper_evaluation_mode = True
    s.broker = "paper"
    s.is_kill_switch_active.return_value = False
    s.log_file = log_file
    s.evaluation_output_dir = eval_dir
    s.evaluation_ledger_file = ledger_file
    return s


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _setup_db(db_path: str):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.api.models import Base
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


async def _seed_trades(session, session_date: str) -> None:
    from app.api.models import DBTradeJournal
    dt = datetime.strptime(session_date, "%Y-%m-%d")
    rows = [
        DBTradeJournal(
            session_date=session_date, strategy_id="orb",
            underlying_symbol="SPY", option_symbol="SPY_SMOKE_CALL_1",
            status="closed", fill_price=3.10, limit_price=3.00,
            exit_price=3.65, realized_pnl=55.0, slippage=0.10,
            bid=3.00, ask=3.10, quantity=1, filled_quantity=1,
            entry_time=dt.replace(hour=9, minute=45),
            exit_time=dt.replace(hour=10, minute=30),
            exit_reason="take_profit", delta=0.42, spread_pct=0.032, is_paper=True,
        ),
        DBTradeJournal(
            session_date=session_date, strategy_id="vwap_reclaim",
            underlying_symbol="QQQ", option_symbol="QQQ_SMOKE_CALL_1",
            status="closed", fill_price=2.55, limit_price=2.50,
            exit_price=3.00, realized_pnl=45.0, slippage=0.05,
            bid=2.50, ask=2.60, quantity=1, filled_quantity=1,
            entry_time=dt.replace(hour=10, minute=15),
            exit_time=dt.replace(hour=11, minute=0),
            exit_reason="take_profit", delta=0.38, spread_pct=0.039, is_paper=True,
        ),
        DBTradeJournal(
            session_date=session_date, strategy_id="orb",
            underlying_symbol="SPY", option_symbol="SPY_SMOKE_PUT_1",
            status="closed", fill_price=1.85, limit_price=1.80,
            exit_price=0.92, realized_pnl=-93.0, slippage=0.05,
            bid=1.80, ask=1.90, quantity=1, filled_quantity=1,
            entry_time=dt.replace(hour=11, minute=30),
            exit_time=dt.replace(hour=13, minute=0),
            exit_reason="stop_loss", delta=0.33, spread_pct=0.053, is_paper=True,
        ),
        DBTradeJournal(
            session_date=session_date, strategy_id="rsi_trend",
            underlying_symbol="IWM", option_symbol=None,
            status="rejected",
            rejection_reason="spread_too_wide: spread 14% exceeds max 10%",
            is_paper=True,
        ),
    ]
    for r in rows:
        session.add(r)
    await session.commit()


async def _seed_pending_order(session, session_date: str) -> str:
    from app.api.models import DBPendingOrder
    oid = "smoke-pending-order-00001"
    session.add(DBPendingOrder(
        order_id=oid, option_symbol="SPY_SMOKE_STALE",
        symbol="SPY", strategy_id="orb", direction="long",
        quantity=1, limit_price=3.00, submitted_at=datetime.utcnow(),
        status="pending", session_date=session_date,
    ))
    await session.commit()
    return oid


async def _seed_session_logs(session, session_date: str) -> None:
    from app.api.models import DBSessionLog
    dt = datetime.strptime(session_date, "%Y-%m-%d")
    for ev, lvl, msg, h, m in [
        ("session_start", "info",    "Session started",           9, 30),
        ("heartbeat",     "info",    "cycle=1",                   9, 35),
        ("error",         "error",   "API timeout on get_quote", 10,  0),
        ("kill_switch",   "warning", "Kill switch activated",    10,  5),
        ("session_end",   "info",    "Session ended",            16,  0),
    ]:
        session.add(DBSessionLog(
            session_date=session_date, event=ev, level=lvl, message=msg,
            timestamp=dt.replace(hour=h, minute=m),
        ))
    await session.commit()


# ── Main ──────────────────────────────────────────────────────────────────────

async def run_smoke_test() -> bool:
    with tempfile.TemporaryDirectory(prefix="eval_smoke_") as tmp:
        tmp = Path(tmp)
        eval_dir = str(tmp / "evaluation")
        ledger_file = str(tmp / "evaluation" / "ledger.json")
        log_file = str(tmp / "logs" / "trading.log")
        (tmp / "logs").mkdir()
        (tmp / "logs" / "trading.log").touch()

        settings = _fake_settings(log_file, eval_dir, ledger_file)
        session_date = str(date.today())

        # ── Step 1: settings guard ────────────────────────────────────────
        _section("Step 1 — Settings & no-live-path guard")
        assert settings.live_trading_enabled is False
        assert settings.paper_evaluation_mode is True
        _ok("live_trading_enabled=false")
        _ok("paper_evaluation_mode=true")
        _ok(f"evaluation_output_dir={eval_dir}")

        try:
            from app.config.settings import Settings
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    Settings(paper_evaluation_mode=True, live_trading_enabled=True)  # type: ignore
                    _fail("eval+live combination should be blocked")
                except Exception:
                    _ok("eval + live_trading_enabled=true raises ValueError")
        except Exception as exc:
            _fail("live path guard", str(exc))

        # ── Step 2: pre-session checklist ─────────────────────────────────
        _section("Step 2 — Pre-session checklist")
        factory, engine = await _setup_db(str(tmp / "smoke.db"))
        async with factory() as session:
            try:
                from app.brokers.paper_broker import PaperBroker
                from app.data.yfinance_data import YFinanceDataSource
                from app.evaluation.pre_session import (
                    all_required_pass, run_pre_session_checks,
                )

                broker = PaperBroker()
                broker.set_data_source(YFinanceDataSource())
                risk = MagicMock()
                risk.daily_pnl = 0.0  # fresh session

                checks = await run_pre_session_checks(
                    settings=settings, broker=broker,
                    db_session=session, risk_manager=risk,
                )
                for c in checks:
                    icon = _PASS if c.passed else (_FAIL if c.required else _WARN)
                    req = "required" if c.required else "advisory"
                    print(f"    {icon}  [{req:8}]  {c.name:35s}  {c.message}")

                if all_required_pass(checks):
                    _ok("all required pre-session checks PASSED")
                else:
                    _fail("required pre-session checks failed",
                          ", ".join(c.name for c in checks if c.required and not c.passed))
            except Exception as exc:
                _fail("pre-session checklist", traceback.format_exc(limit=2))

        # ── Step 3: seed test data ────────────────────────────────────────
        _section("Step 3 — Seed test data")
        async with factory() as session:
            try:
                await _seed_trades(session, session_date)
                _ok("3 closed trades + 1 rejection seeded")
                pending_id = await _seed_pending_order(session, session_date)
                _ok(f"pending order seeded  [{pending_id}]")
                await _seed_session_logs(session, session_date)
                _ok("session logs seeded  (heartbeat, error, kill_switch)")
            except Exception as exc:
                _fail("seed data", str(exc))
                pending_id = "smoke-pending-order-00001"

        # ── Step 4: cancellation + stale cleanup ──────────────────────────
        _section("Step 4 — Pending order cancellation")
        broker_mock = MagicMock()
        cancel_calls: list[str] = []

        async def _mock_cancel(oid: str) -> bool:
            cancel_calls.append(oid)
            return True

        broker_mock.cancel_order = _mock_cancel
        broker_mock.get_positions = AsyncMock(return_value=[])

        pending_mock = MagicMock()
        pending_mock.order_id = pending_id
        fill_tracker_mock = MagicMock()
        fill_tracker_mock.pending_orders.return_value = [pending_mock]

        from app.evaluation.post_session import run_post_session
        async with factory() as session:
            post_result = await run_post_session(
                settings=settings, broker=broker_mock, db_session=session,
                fill_tracker=fill_tracker_mock, pm=None, journal=None,
                alert_service=None, session_date=session_date,
            )

        if cancel_calls and cancel_calls[0] == pending_id:
            _ok(f"broker.cancel_order called  [{cancel_calls[0]}]")
        else:
            _fail("broker.cancel_order not called", f"calls={cancel_calls}")

        # ── Step 5: daily report ──────────────────────────────────────────
        _section("Step 5 — Daily evaluation report")
        errors_present = bool(post_result.errors)

        json_path = Path(post_result.report_path_json) if post_result.report_path_json else None
        md_path = Path(post_result.report_path_md) if post_result.report_path_md else None

        if json_path and json_path.exists():
            _ok(f"JSON report  → {json_path}")
        else:
            _fail("JSON report not written", str(post_result.errors))
        if md_path and md_path.exists():
            _ok(f"Markdown report  → {md_path}")
        else:
            _fail("Markdown report not written")

        if json_path and json_path.exists():
            rep = json.loads(json_path.read_text())
            tf = rep.get("trades_filled", 0)
            pnl = rep.get("realized_pnl", 0.0)
            wr = rep.get("win_rate")
            dd = rep.get("max_drawdown", 0.0)
            ae = rep.get("api_errors", 0)
            ks = rep.get("kill_switch_events", 0)

            _ok(f"trades_filled={tf}")
            if tf == 3:
                _ok("  trades_filled assertion OK (==3)")
            else:
                _fail(f"  trades_filled expected 3, got {tf}")

            _ok(f"realized_pnl=${pnl:.2f}")
            if abs(pnl - 7.0) < 0.01:
                _ok("  realized_pnl assertion OK (== $7.00)")
            else:
                _fail(f"  realized_pnl expected $7.00, got ${pnl:.2f}")

            if wr is not None:
                _ok(f"win_rate={wr:.1%}")
                if abs(wr - 2/3) < 0.01:
                    _ok("  win_rate assertion OK (== 66.7%)")
                else:
                    _fail(f"  win_rate expected 66.7%, got {wr:.1%}")
            else:
                _fail("win_rate is None")

            _ok(f"max_drawdown=${dd:.2f}  api_errors={ae}  kill_switch_events={ks}")

            if ae == 1:
                _ok("  api_errors assertion OK (==1)")
            else:
                _fail(f"  api_errors expected 1, got {ae}")

            if ks == 1:
                _ok("  kill_switch_events assertion OK (==1)")
            else:
                _fail(f"  kill_switch_events expected 1, got {ks}")

            by_strat = {s["strategy_id"]: s for s in rep.get("by_strategy", [])}
            if "orb" in by_strat and "vwap_reclaim" in by_strat:
                _ok("per-strategy breakdown  (orb, vwap_reclaim present)")
            else:
                _fail("per-strategy breakdown missing", f"keys={list(by_strat)}")

            notes = rep.get("notes", [])
            recs = rep.get("recommendations", [])
            _ok(f"notes={len(notes)}  recommendations={len(recs)}")

        # ── Step 6: ledger ────────────────────────────────────────────────
        _section("Step 6 — Evaluation ledger")
        ledger_path = Path(ledger_file)
        if post_result.ledger_updated and ledger_path.exists():
            _ok(f"ledger.json written  → {ledger_path}")
            ld = json.loads(ledger_path.read_text())
            cum = ld.get("cumulative", {})
            _ok(f"version={ld.get('version')}  trading_days={cum.get('trading_days')}  "
                f"total_pnl=${cum.get('total_pnl'):.2f}")
            if cum.get("trading_days") == 1:
                _ok("  trading_days assertion OK (==1)")
            else:
                _fail(f"  trading_days expected 1, got {cum.get('trading_days')}")
            pbs = cum.get("pnl_by_strategy", {})
            if pbs:
                _ok(f"pnl_by_strategy keys: {sorted(pbs)}")
            else:
                _fail("pnl_by_strategy empty")
        else:
            _fail("ledger not updated", str(post_result.errors))

        # ── Step 7: second session accumulates ────────────────────────────
        _section("Step 7 — Second session accumulates (trading_days == 2)")
        db2_path = str(tmp / "smoke2.db")
        factory2, engine2 = await _setup_db(db2_path)
        session_date_2 = "2024-03-19"
        async with factory2() as session2:
            await _seed_trades(session2, session_date_2)
            post2 = await run_post_session(
                settings=settings, db_session=session2,
                session_date=session_date_2,
            )
        await engine2.dispose()

        if ledger_path.exists():
            ld2 = json.loads(ledger_path.read_text())
            days = ld2["cumulative"]["trading_days"]
            _ok(f"ledger now has {days} trading day(s)")
            if days == 2:
                _ok("  trading_days assertion OK (==2)")
            else:
                _fail(f"  trading_days expected 2, got {days}")
        else:
            _fail("ledger not found after second session")

        # ── Step 8: alert service no-op ───────────────────────────────────
        _section("Step 8 — Alert service (no-op when unconfigured)")
        try:
            from app.utils.alerting import AlertConfig, AlertEvent, AlertService
            svc = AlertService()
            assert not svc.is_configured
            _ok("AlertService not configured  (no channels)")
            await svc.send(AlertEvent.SESSION_SUMMARY, "smoke test message")
            _ok("send() returns cleanly when no channels configured")
        except Exception as exc:
            _fail("AlertService", str(exc))

        # ── Step 9: /health endpoint ──────────────────────────────────────
        _section("Step 9 — /health endpoint")
        try:
            from httpx import AsyncClient, ASGITransport
            from app.api.dashboard_api import create_app
            from app.api.models import get_db
            from sqlalchemy.ext.asyncio import async_sessionmaker as _maker

            test_app = create_app()
            test_factory = _maker(engine, expire_on_commit=False)

            async def _override_db():
                async with test_factory() as s:
                    yield s

            test_app.dependency_overrides[get_db] = _override_db

            async with AsyncClient(
                transport=ASGITransport(app=test_app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/health")

            assert resp.status_code == 200, f"Status {resp.status_code}"
            h = resp.json()
            _ok(f"GET /health → 200  status={h['status']}")
            _ok(f"paper_mode={h['paper_mode']}")
            _ok(f"paper_evaluation_mode={h.get('paper_evaluation_mode', 'MISSING')}")
            _ok(f"kill_switch_active={h['kill_switch_active']}")
            _ok(f"database.status={h['database']['status']}")

            assert h["paper_mode"] is True, "paper_mode must be True"
            assert "paper_evaluation_mode" in h, "/health missing paper_evaluation_mode"
            assert h["kill_switch_active"] is False
            assert h["database"]["status"] == "ok"
            _ok("all /health field assertions passed")

        except Exception as exc:
            _fail("/health", traceback.format_exc(limit=3))

        await engine.dispose()

    # ── Summary ───────────────────────────────────────────────────────────────
    _section("Summary")
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(f"\n  Total checks: {total}   Passed: {passed}   Failed: {failed}")
    if failed == 0:
        print(f"\n  {_PASS}  All smoke test checks PASSED\n")
    else:
        print(f"\n  {_FAIL}  {failed} check(s) FAILED:\n")
        for name, ok, detail in _results:
            if not ok:
                print(f"    • {name}" + (f" — {detail}" if detail else ""))
        print()

    return failed == 0


if __name__ == "__main__":
    print(f"\n{'═'*60}")
    print("  Paper Evaluation Smoke Test")
    print(f"{'═'*60}")
    success = asyncio.run(run_smoke_test())
    sys.exit(0 if success else 1)
