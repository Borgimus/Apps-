"""Regressions for the September 8 quote clock and observation-report defects."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.models import Base, DBSessionLog, DBSignalBridge
from app.config import Settings
from app.evaluation.daily_report import build_daily_report, to_markdown
from app.evaluation.ledger import EvaluationLedger
from app.evaluation.post_session import _ledger_file_for_settings
from app.evaluation.session_context import capture_session_context, context_from_logs
from app.evaluation.shadow_book import SHADOW_MODEL_VERSION, ShadowBook

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 8, 11, 0, 15, 360783, tzinfo=ET)


def config(tmp_path):
    return Settings(_env_file=None, broker="alpaca", live_trading_enabled=False,
                    paper_evaluation_mode=True, paper_eval_permissive_entry_mode=True,
                    paper_scaled_sizing_enabled=True, paper_scaled_guardrails_enabled=True,
                    paper_scaled_shadow_only_strategies="orb,vwap_reclaim",
                    options_data_provider="tradier", entry_order_timeout_secs=120,
                    kill_switch_file=str(tmp_path / "KILL_SWITCH"))


def book(tmp_path, clock):
    return ShadowBook(config(tmp_path), events_path=tmp_path / "events.jsonl",
                      state_path=tmp_path / "state.json", clock=clock)


def record(sb, symbol="IWM", ask=1.1, quote_time=NOW):
    sb.record_signal(now=None, strategy_id="orb", symbol=symbol, direction="long",
                     executed=False, block_reason="strategy_shadow_only",
                     option_symbol=symbol + "_TEST", limit_price=1.1, entry_ask=ask,
                     quality_score=4, market_regime="long",
                     contract_metadata={"bid": 1.05, "ask": ask,
                                        "quote_timestamp": quote_time.isoformat(),
                                        "quote_feed": "tradier_opra", "liquidity_passed": True})


def quote(at, bid=1.05, ask=1.1):
    return SimpleNamespace(timestamp=at, bid=bid, ask=ask, feed="tradier_opra")


def events(tmp_path):
    return [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]


def test_iwm_quote_received_after_cycle_start_validates_at_observation(tmp_path):
    received = NOW.replace(second=17)
    sb = book(tmp_path, lambda: received)
    qt = NOW.replace(second=16, microsecond=0)
    record(sb, quote_time=qt)
    pos = next(iter(sb._open.values()))
    assert pos.fill_validated_at == received.isoformat()
    assert pos.last_quote_timestamp == qt.isoformat()
    assert events(tmp_path)[0]["ts"] == received.isoformat()


@pytest.mark.asyncio
async def test_each_quote_samples_clock_after_request(tmp_path):
    clock = [NOW]
    sb = book(tmp_path, lambda: clock[0])
    record(sb, "IWM", ask=1.2)
    record(sb, "RIOT", ask=1.2)

    async def fetch(symbol):
        clock[0] += timedelta(seconds=2)
        return quote(clock[0])

    await sb.update(SimpleNamespace(get_option_quote=AsyncMock(side_effect=fetch)))
    assert [p.fill_validated_at for p in sb._open.values() if p.variant == "baseline"] == [
        (NOW + timedelta(seconds=2)).isoformat(),
        (NOW + timedelta(seconds=4)).isoformat(),
    ]


@pytest.mark.asyncio
async def test_request_finishing_after_deadline_cannot_fill(tmp_path):
    clock = [NOW]
    sb = book(tmp_path, lambda: clock[0])
    record(sb, ask=1.2)
    clock[0] += timedelta(seconds=119)

    async def fetch(symbol):
        clock[0] += timedelta(seconds=2)
        return quote(clock[0] - timedelta(seconds=1))

    await sb.update(SimpleNamespace(get_option_quote=AsyncMock(side_effect=fetch)))
    assert sb.open_count() == 0
    result = events(tmp_path)[-1]
    assert result["event"] == "shadow_unfilled" and result["shadow_pnl"] is None
    assert result["ts"] == clock[0].isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [-61, 1])
async def test_actual_stale_or_future_quote_still_rejected(tmp_path, offset):
    clock = [NOW]
    sb = book(tmp_path, lambda: clock[0])
    record(sb, ask=1.2)

    async def fetch(symbol):
        clock[0] += timedelta(seconds=2)
        return quote(clock[0] + timedelta(seconds=offset))

    await sb.update(SimpleNamespace(get_option_quote=AsyncMock(side_effect=fetch)))
    assert not next(iter(sb._open.values())).fill_validated


@pytest.mark.asyncio
async def test_exit_timestamp_uses_quote_receipt_time(tmp_path):
    clock = [NOW]
    sb = book(tmp_path, lambda: clock[0])
    record(sb)

    async def fetch(symbol):
        clock[0] += timedelta(seconds=3)
        return quote(clock[0], bid=.5, ask=.52)

    await sb.update(SimpleNamespace(get_option_quote=AsyncMock(side_effect=fetch)))
    result = events(tmp_path)[-1]
    assert result["exit_reason"] == "stop_loss"
    assert result["ts"] == clock[0].isoformat()


def test_model_two_state_archived_without_resuming(tmp_path):
    raw = '{"model_version":"2","seq":5,"open":[{}],"episodes":{}}'
    (tmp_path / "state.json").write_text(raw)
    (tmp_path / "state.json.legacy-v1").write_text("preserved v1")
    sb = book(tmp_path, lambda: NOW)
    assert sb.open_count() == 0 and SHADOW_MODEL_VERSION == "4"
    assert (tmp_path / "state.json.legacy-v2").read_text() == raw
    assert (tmp_path / "state.json.legacy-v1").read_text() == "preserved v1"


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_report_separates_repeated_signals_and_rsi_diagnostics(db, tmp_path):
    for i in range(3):
        at = NOW + timedelta(seconds=i * 30)
        for strategy, symbol, age, reason in [
            ("orb", "MSFT", 300, "strategy_shadow_only"),
            ("vwap_reclaim", "IWM", 120, "signal_quality_below_min"),
            ("rsi_trend", "GOOGL", 345600, "rsi_trend_diagnostic_only"),
        ]:
            db.add(DBSignalBridge(session_date="2026-09-08", timestamp=at,
                                  symbol=symbol, strategy_id=strategy, signal_direction="long",
                                  signal_age_seconds=age+i*30, signal_quality_score=4,
                                  final_decision="skipped" if strategy=="rsi_trend" else "blocked",
                                  exact_block_reason=reason, orb_fwd_pct_5m=-.01+i*.01))
    await db.commit()
    report = await build_daily_report(db, "2026-09-08", config(tmp_path))
    assert report.total_signals == 2
    assert report.bridge_entries_count == 9
    assert report.entry_signal_observations == 6 and report.diagnostic_signal_observations == 3
    assert report.orb_signals_total == 1 and report.orb_signal_observations == 3
    assert report.orb_avg_fwd_pct_5m == -.01
    stats = {s.strategy_id:s for s in report.by_strategy}
    assert stats["rsi_trend"].signals == 0 and stats["rsi_trend"].diagnostic_observations == 3
    assert "Raw bridge observations | 9" in to_markdown(report)
    assert report.options_data_provider == "unrecorded"


@pytest.mark.asyncio
async def test_captured_cohort_and_late_start_survive_report_and_ledger(db, tmp_path):
    cfg = config(tmp_path)
    started = NOW.replace(hour=10, minute=22, second=0, microsecond=0)
    context = capture_session_context(cfg, started, ["orb","vwap_reclaim","rsi_trend"])
    assert context["evaluation_cohort"] == "guardrails_v3_tradier_options_2026_09_08"
    assert context["broker_entry_strategies"] == []
    assert context["start_delay_seconds"] == 52*60
    db.add(DBSessionLog(session_date="2026-09-08", timestamp=started,
                        event="session_context", data_json=json.dumps(context)))
    await db.commit()
    # Rebuilding after an environment change must keep the original provenance.
    cfg.options_data_provider = "alpaca"
    report = await build_daily_report(db, "2026-09-08", cfg)
    assert report.options_data_provider == "tradier" and report.session_labels == ["late_start"]
    assert "52.0 minutes" in to_markdown(report)
    assert "Strategies permitted to submit entries:** none" in to_markdown(report)
    cfg.evaluation_ledger_file = str(tmp_path / "ledger.json")
    path = _ledger_file_for_settings(cfg, report)
    assert "options_tradier" in path and context["evaluation_cohort"] in path
    ledger = EvaluationLedger(path)
    ledger.add_session(report, trade_records=[])
    ledger.save()
    restored = EvaluationLedger.load(path).sessions[0]
    assert restored.evaluation_cohort == context["evaluation_cohort"]
    assert restored.options_data_provider == "tradier" and restored.session_labels == ["late_start"]
    assert not Path(_ledger_file_for_settings(cfg)).exists()


def test_mixed_restart_context_is_flagged(tmp_path):
    first = capture_session_context(config(tmp_path), NOW)
    changed = dict(first, options_data_provider="alpaca")
    rows = [SimpleNamespace(event="session_context", data_json=json.dumps(c)) for c in (first,changed)]
    result = context_from_logs(rows)
    assert result["evaluation_cohort"] == "mixed_session_context"
    assert "mixed_session_context" in result["session_labels"]


def test_scheduled_start_has_small_delay_without_late_label(tmp_path):
    at = NOW.replace(hour=9, minute=30, second=4, microsecond=0)
    context = capture_session_context(config(tmp_path), at.astimezone(ZoneInfo("UTC")))
    assert context["start_delay_seconds"] == 4 and context["session_labels"] == []
