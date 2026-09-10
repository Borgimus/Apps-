"""Regressions from the September evaluation audit; no broker/network access."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from app.brokers.alpaca_broker import AlpacaBroker
from app.brokers.broker_interface import OptionQuote
from app.config import Settings
from app.evaluation.daily_report import DailyReport, StrategyStats
from app.evaluation.ledger import EvaluationLedger
from app.evaluation.shadow_book import ShadowBook
from app.risk.paper_sizing import calculate_paper_scaled_quantity
from app.trading.position_manager import PositionManager
from scripts.rebuild_evaluation_ledger import rebuild

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 8, 10, tzinfo=ET)


def settings(tmp_path):
    return Settings(broker="paper", live_trading_enabled=False,
                    paper_evaluation_mode=True, paper_scaled_sizing_enabled=True,
                    paper_scaled_exit_variant_shadow_enabled=False,
                    universe={"max_contracts_per_position": 10},
                    kill_switch_file=str(tmp_path / "KILL_SWITCH"))


def book(tmp_path, cfg=None):
    return ShadowBook(cfg or settings(tmp_path), events_path=tmp_path / "events.jsonl",
                      state_path=tmp_path / "state.json")


def record(sb, now=NOW, **overrides):
    args = dict(now=now, strategy_id="orb", symbol="SPY", direction="long",
                executed=False, block_reason="strategy_shadow_only", option_symbol="SPY_TEST",
                limit_price=1.0, entry_ask=1.0, quality_score=4, market_regime="long",
                contract_metadata={"bid": 0.98, "quote_timestamp": now.isoformat(),
                                   "quote_feed": "opra", "liquidity_passed": True})
    args.update(overrides)
    return sb.record_signal(**args)


def broker(bid, now, ask=None, feed="opra", timestamp=True):
    q = OptionQuote("SPY_TEST", Decimal(str(bid)), Decimal(str(ask if ask is not None else bid + .02)),
                    Decimal(str(bid)), 1000, 1000, .2, .4,
                    now if timestamp is True else timestamp, feed)
    return SimpleNamespace(get_option_quote=AsyncMock(return_value=q))


def events(tmp_path, kind):
    return [e for e in map(json.loads, (tmp_path / "events.jsonl").read_text().splitlines()) if e["event"] == kind]


@pytest.mark.asyncio
@pytest.mark.parametrize("prices,expected", [
    ([.7], None), ([1.249, .8], None), ([1.25, .9375], "trailing_stop"),
    ([.5], "stop_loss"), ([2.0], "take_profit"),
])
async def test_shadow_matches_real_position_exit_policy(tmp_path, prices, expected):
    cfg = settings(tmp_path)
    sb, pm = book(tmp_path, cfg), PositionManager(cfg)
    record(sb)
    pm.open("SPY_TEST", "SPY", "orb", "long", NOW, 1.0, 2)
    for i, price in enumerate(prices):
        now = NOW + timedelta(seconds=30 * (i + 1))
        pm.update_price("SPY_TEST", price)
        actual = pm.should_exit("SPY_TEST", price, now)
        await sb.update(broker(price, now), now)
        closes = events(tmp_path, "shadow_close")
        assert (closes[-1]["exit_reason"] if closes else None) == actual
    assert actual == expected


@pytest.mark.asyncio
async def test_activation_environment_override_is_shared(tmp_path, monkeypatch):
    monkeypatch.setenv("POSITION_TRAILING_ACTIVATION_PCT", "0.5")
    cfg = settings(tmp_path)
    sb, pm = book(tmp_path, cfg), PositionManager(cfg)
    record(sb)
    pm.open("SPY_TEST", "SPY", "orb", "long", NOW, 1, 2)
    for i, price in enumerate([1.3, .9]):
        now = NOW + timedelta(seconds=30 * (i + 1))
        pm.update_price("SPY_TEST", price)
        assert pm.should_exit("SPY_TEST", price, now) is None
        await sb.update(broker(price, now), now)
    assert not events(tmp_path, "shadow_close")


@pytest.mark.asyncio
async def test_eod_is_eastern_when_caller_uses_utc(tmp_path):
    sb = book(tmp_path)
    entry = NOW.replace(hour=15, minute=20).astimezone(timezone.utc)
    record(sb, now=entry)
    now = entry + timedelta(minutes=25)
    await sb.update(broker(1.1, now), now)
    assert events(tmp_path, "shadow_close")[0]["exit_reason"] == "eod_exit"


@pytest.mark.asyncio
async def test_prefill_peaks_and_duration_do_not_contaminate_position(tmp_path):
    sb = book(tmp_path)
    record(sb, entry_ask=1.1)
    await sb.update(broker(1.5, NOW + timedelta(seconds=30)), NOW + timedelta(seconds=30))
    sp = next(iter(sb._open.values()))
    assert sp.peak_price == 1
    fill_time = NOW + timedelta(minutes=1)
    await sb.update(broker(.98, fill_time, ask=1), fill_time)
    assert sp.fill_validated_at == fill_time.isoformat()
    assert not sp.trailing_stop_armed
    now = NOW + timedelta(minutes=120)
    await sb.update(broker(.8, now), now)
    assert sb.open_count() == 1
    now += timedelta(minutes=1)
    await sb.update(broker(.8, now), now)
    assert events(tmp_path, "shadow_close")[0]["exit_reason"] == "max_hold"


@pytest.mark.asyncio
@pytest.mark.parametrize("feed,timestamp,bid,ask", [
    ("indicative", True, .98, 1), (None, True, .98, 1), ("opra", None, .98, 1),
    ("opra", NOW - timedelta(seconds=61), .98, 1),
    ("opra", NOW + timedelta(seconds=10), .98, 1),
    ("opra", True, 0, 1), ("opra", True, 1.1, 1),
])
async def test_untrusted_quote_cannot_validate_fill(tmp_path, feed, timestamp, bid, ask):
    sb = book(tmp_path)
    record(sb, contract_metadata={})
    await sb.update(broker(bid, NOW, ask=ask, feed=feed, timestamp=timestamp), NOW)
    assert not next(iter(sb._open.values())).fill_validated
    await sb.update(broker(.9, NOW), NOW + timedelta(seconds=121))
    assert sb.open_count() == 0
    assert events(tmp_path, "shadow_unfilled")[0]["shadow_pnl"] is None


@pytest.mark.asyncio
async def test_stale_session_end_mark_is_unpriced(tmp_path):
    sb = book(tmp_path)
    record(sb)
    sb.close_all(NOW + timedelta(minutes=10))
    close = events(tmp_path, "shadow_close")[0]
    assert close["shadow_pnl"] is None and close["category"] == "unpriced"


def test_unpriceable_episode_retries_without_double_counting(tmp_path):
    sb = book(tmp_path)
    record(sb, option_symbol=None, limit_price=None, entry_ask=None)
    record(sb, now=NOW + timedelta(seconds=30))
    record(sb, now=NOW + timedelta(seconds=60))
    signals = events(tmp_path, "signal")
    assert [s["new_opportunity"] for s in signals] == [True, False, False]
    assert len({s["opportunity_id"] for s in signals}) == 1
    assert sb.open_count() == 1


@pytest.mark.parametrize("overrides,reason", [
    ({"limit_price": 2.51, "entry_ask": 2.5}, "premium_budget_or_price_invalid"),
    ({"quality_score": 2}, "signal_quality_below_min"),
    ({"market_regime": "short"}, "market_regime_mismatch"),
    ({"symbol": "QQQ"}, "symbol_disabled"),
    ({"contract_metadata": {}}, "contract_filters_unverified"),
])
def test_ineligible_signals_are_labelled_diagnostics(tmp_path, overrides, reason):
    sb = book(tmp_path)
    record(sb, **overrides)
    event = events(tmp_path, "signal")[0]
    assert not event["entry_filter_eligible"]
    assert reason in event["entry_filter_reasons"]
    assert event["portfolio_validated"] is False
    assert event["counts_toward_readiness"] is False


def test_single_contract_cannot_have_partial_exit_variant(tmp_path):
    sb = book(tmp_path)
    record(sb, limit_price=2, entry_ask=2, variant="partial_25_breakeven")
    assert sb.open_count() == 0
    assert events(tmp_path, "variant_not_executable")[0]["quantity"] == 1


@pytest.mark.asyncio
async def test_partial_variant_uses_whole_contracts(tmp_path):
    sb = book(tmp_path)
    record(sb, limit_price=.7, entry_ask=.7, contract_metadata={
        "bid": .68, "quote_timestamp": NOW.isoformat(), "quote_feed": "opra", "liquidity_passed": True,
    }, variant="partial_25_breakeven")
    now = NOW + timedelta(seconds=30)
    await sb.update(broker(.875, now), now)
    sp = next(iter(sb._open.values()))
    assert sp.quantity == 3
    assert sp.remaining_fraction == pytest.approx(2 / 3)
    now += timedelta(seconds=30)
    await sb.update(broker(.7, now), now)
    close = events(tmp_path, "shadow_close")[0]
    assert close["sized_shadow_pnl"] == pytest.approx(17.5, abs=.02)


@pytest.mark.asyncio
async def test_restart_preserves_peaks_and_activation(tmp_path):
    now = datetime.now(ET).replace(hour=10, minute=0, second=0, microsecond=0)
    sb = book(tmp_path)
    record(sb, now=now)
    now += timedelta(seconds=30)
    await sb.update(broker(1.4, now), now)
    restarted = book(tmp_path)
    restored = next(iter(restarted._open.values()))
    assert restored.peak_price == 1.4 and restored.trailing_stop_armed
    now += timedelta(seconds=30)
    await restarted.update(broker(1.04, now), now)
    assert events(tmp_path, "shadow_close")[0]["exit_reason"] == "trailing_stop"


def test_old_shadow_state_is_preserved_without_resuming(tmp_path):
    raw = '{"seq": 4, "open": [{}], "episodes": {}}'
    (tmp_path / "state.json").write_text(raw)
    sb = book(tmp_path)
    assert sb.open_count() == 0
    assert (tmp_path / "state.json.legacy-v1").read_text() == raw


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp", [None, "invalid", "2026-09-08T10:00:00"])
async def test_alpaca_missing_timestamp_stays_missing(tmp_path, timestamp):
    adapter = AlpacaBroker.__new__(AlpacaBroker)
    adapter._options_feed = "opra"
    response = SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {
        "snapshots": {"SPY_TEST": {"latestQuote": {"bp": .98, "ap": 1, "t": timestamp}}},
    })
    adapter._data_client = SimpleNamespace(get=AsyncMock(return_value=response))
    quote = await adapter.get_option_quote("SPY_TEST")
    assert quote.timestamp is None and quote.feed == "opra"
    assert adapter._data_client.get.call_args.kwargs["params"]["feed"] == "opra"
    await adapter._fetch_snapshots(["SPY_TEST"])
    assert adapter._data_client.get.call_args.kwargs["params"]["feed"] == "opra"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_sizing_fails_closed(value):
    assert calculate_paper_scaled_quantity(value, 250, 10) == 0
    assert calculate_paper_scaled_quantity(1, value, 10) == 0


def add_session(ledger, day, pnls):
    report = DailyReport(date=day, session_start=None, session_end=None,
                        realized_pnl=sum(pnls), by_strategy=[StrategyStats(
                            strategy_id="orb", wins=sum(p > 0 for p in pnls),
                            losses=sum(p < 0 for p in pnls), realized_pnl=sum(pnls))])
    records = [SimpleNamespace(status="closed", realized_pnl=p, strategy_id="orb") for p in pnls]
    return ledger.add_session(report, records)


def test_complete_trade_profit_factor_counts_flats_by_strategy():
    ledger = EvaluationLedger()
    entry = add_session(ledger, "2026-09-08", [30, -10, 0])
    stats = ledger.compute_cumulative()
    assert entry.total_trades == 3 and entry.by_strategy["orb"]["trades"] == 3
    assert entry.by_strategy["orb"]["breakevens"] == 1
    assert stats["profit_factor"] == 3 and stats["expectancy"] == 6.67


def test_mixed_legacy_and_complete_metrics_never_show_partial_profit_factor(tmp_path):
    ledger = EvaluationLedger(str(tmp_path / "ledger.json"))
    add_session(ledger, "2026-09-08", [30, -10])
    old = add_session(ledger, "2026-09-09", [-100])
    old.trade_metrics_complete = None
    old.gross_losses = 0
    ledger.save()
    stats = EvaluationLedger.load(str(ledger.ledger_file)).compute_cumulative()
    assert stats["profit_factor"] is None and stats["expectancy"] is None
    assert stats["win_rate"] is None and stats["total_trades"] is None
    assert stats["incomplete_session_dates"] == ["2026-09-09"]


def test_report_trade_mismatch_is_incomplete():
    ledger = EvaluationLedger()
    entry = add_session(ledger, "2026-09-08", [20, -10])
    entry.realized_pnl = 200
    assert ledger.compute_cumulative()["trade_metrics_complete"] is False


def test_rebuild_reads_backup_preserves_source_and_order_ids(tmp_path):
    database = tmp_path / "backup.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE trade_journal (id INTEGER, session_date TEXT, exit_time TEXT, status TEXT, strategy_id TEXT, realized_pnl REAL, fill_price REAL, exit_price REAL, quantity INTEGER, order_id TEXT, exit_order_id TEXT)")
        db.execute("INSERT INTO trade_journal VALUES (1, '2026-09-08', '2026-09-08T11:00:00', 'closed', 'orb', 20, 1, 1.1, 2, 'entry-1', 'exit-1')")
        db.execute("INSERT INTO trade_journal VALUES (2, '2026-09-08', NULL, 'rejected', 'orb', 0, NULL, NULL, NULL, NULL, NULL)")
    before = database.read_bytes()
    output = tmp_path / "rebuilt.json"
    result = rebuild(database, tmp_path / "missing-ledger.json", output)
    data = json.loads(output.read_text())
    assert data["cumulative"]["total_trades"] == 1
    assert data["sessions"][0]["breakevens"] == 0
    assert next(r for r in result["trade_records"] if r["status"] == "closed")["order_id"] == "entry-1"
    assert result["broker_fills_reconciled"] is False
    assert database.read_bytes() == before
    with pytest.raises(ValueError, match="new path"):
        rebuild(database, tmp_path / "missing-ledger.json", output)


@pytest.mark.asyncio
async def test_health_report_separates_flats_and_missing_pnl():
    from app.trading.health_report import HealthReporter
    reporter = HealthReporter(None)
    closed = [SimpleNamespace(strategy_id="orb", realized_pnl=p) for p in (10, -5, 0, None)]
    reporter._trades_by_status = AsyncMock(side_effect=lambda day, status: closed if status == "closed" else [])
    reporter._all_pending_orders = AsyncMock(return_value=[])
    reporter._count_stale_cancels = AsyncMock(return_value=0)
    result = await reporter.generate("2026-09-08")
    assert result["trades"]["losses"] == 1
    assert result["trades"]["breakevens"] == 1
    assert result["trades"]["missing_pnl"] == 1
    assert result["trades"]["win_rate"] is None
    assert result["by_strategy"]["orb"]["breakevens"] == 1
    assert result["avg_loss"] == -5


def test_shadow_report_excludes_legacy_and_unpriced_results(tmp_path, monkeypatch, capsys):
    from scripts import shadow_report
    sb = book(tmp_path)
    record(sb)
    sb.close_all(NOW + timedelta(minutes=10))
    with (tmp_path / "events.jsonl").open("a") as stream:
        stream.write(json.dumps({"event": "shadow_close", "shadow_pnl": 9999, "signal_id": "legacy"}) + "\n")
    monkeypatch.setattr("sys.argv", ["shadow_report", "--events", str(tmp_path / "events.jsonl")])
    shadow_report.main()
    output = capsys.readouterr().out
    assert "unpriced closes: 1" in output
    assert "excluded other-version events: 1" in output
    assert "9999" not in output
    assert "no portfolio outcome is inferred" in output
