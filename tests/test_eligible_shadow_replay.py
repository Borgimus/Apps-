"""First-eligible entry, matched quotes and chronological portfolio regressions."""
import copy
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.evaluation.session_context import capture_session_context
from app.evaluation.shadow_book import ShadowBook
from app.evaluation.shadow_replay import ET, policy_hash, replay_session
from app.evaluation.shadow_summary import observation_progress, read_events, summarize, to_markdown

DAY = "2026-09-09"


def at(hhmm):
    return datetime.fromisoformat(f"{DAY}T{hhmm}").replace(tzinfo=ET)


def config(tmp_path):
    s = Settings(_env_file=None, broker="alpaca", live_trading_enabled=False,
                 paper_evaluation_mode=True, paper_eval_permissive_entry_mode=True,
                 paper_scaled_sizing_enabled=True, paper_scaled_guardrails_enabled=True,
                 paper_scaled_shadow_only_strategies="orb,vwap_reclaim",
                 paper_scaled_exit_variant_shadow_enabled=True,
                 options_data_provider="tradier", entry_order_timeout_secs=120,
                 kill_switch_file=str(tmp_path / "KILL_SWITCH"))
    s.universe.max_contracts_per_position = 10
    s.position.stop_loss_pct = .5
    s.position.take_profit_pct = 1.0
    s.position.trailing_stop_pct = .25
    s.position.trailing_activation_pct = .25
    s.position.max_hold_minutes = 120
    s.position.eod_exit_time = "12:30"
    return s


def context(s):
    c = capture_session_context(s, at("09:30:10"), ("orb", "vwap_reclaim", "rsi_trend"))
    return dict(c, starting_equity=100_000.0)


def setup_book(tmp_path):
    s = config(tmp_path)
    c = context(s)
    sb = ShadowBook(s, tmp_path / "events.jsonl", tmp_path / "state.json",
                    clock=lambda: at("09:30:10"), session_context=c)
    return sb, c


def record(sb, now, *, price=1.82, regime="short", symbol="IWM", signal_time=None, quality=4, ask=None):
    ask = price if ask is None else ask
    sb.record_signal(
        now=now, strategy_id="orb", symbol=symbol, direction="short",
        executed=False, block_reason="strategy_shadow_only", option_symbol=f"{symbol}_TEST",
        limit_price=price, entry_ask=ask, quality_score=quality, market_regime=regime,
        signal_timestamp=signal_time or now - timedelta(minutes=5),
        runtime_gates={"reconciliation_clear": True, "kill_switch_clear": True},
        contract_metadata={"bid": price - .01, "ask": ask, "liquidity_passed": True,
                           "quote_timestamp": now.isoformat(), "quote_feed": "tradier_opra"},
    )


def broker(now, bid, ask):
    return SimpleNamespace(get_option_quote=AsyncMock(return_value=SimpleNamespace(
        bid=bid, ask=ask, timestamp=now, feed="tradier_opra")))


def test_iwm_first_eligible_anchor_does_not_reuse_early_regime_blocked_entry(tmp_path):
    sb, _ = setup_book(tmp_path)
    record(sb, at("10:35:24"), price=1.79, regime="neutral", signal_time=at("10:30:00"))
    assert not [p for p in sb._open.values() if p.channel == "eligible"]
    record(sb, at("10:39:44"), signal_time=at("10:30:00"))
    record(sb, at("10:39:55"), price=1.83, signal_time=at("10:30:00"))
    entries = [r for r in read_events(tmp_path / "events.jsonl") if r["event"] == "eligible_entry"]
    assert len(entries) == 1
    entry = entries[0]
    assert (entry["entry_price"], entry["quantity"], entry["entry_time"]) == (1.82, 1, at("10:39:44").isoformat())
    early = next(p for p in sb._open.values() if p.channel == "diagnostic" and p.variant == "baseline")
    assert early.entry_price == 1.79 and early.opportunity_id == entry["opportunity_id"]
    assert early.pair_id != entry["pair_id"]
    eligible = [p for p in sb._open.values() if p.channel == "eligible"]
    assert {p.variant for p in eligible} == {"baseline", "breakeven_25"}
    assert all(p.entry_time == entry["entry_time"] and p.entry_price == 1.82 for p in eligible)


@pytest.mark.parametrize("quality,regime,price,age", [(2, "short", 1.0, 5), (4, "neutral", 1.0, 5),
                                                     (4, "short", 2.51, 5), (4, "short", 1.0, 11)])
def test_rejected_or_stale_observation_cannot_create_eligible_anchor(tmp_path, quality, regime, price, age):
    sb, _ = setup_book(tmp_path)
    record(sb, at("10:30:00"), quality=quality, regime=regime, price=price,
           signal_time=at("10:30:00") - timedelta(minutes=age))
    assert not any(r["event"] == "eligible_entry" for r in read_events(tmp_path / "events.jsonl"))


@pytest.mark.asyncio
async def test_matched_variants_use_one_entry_and_one_quote_and_size_whole_partial_contracts(tmp_path):
    sb, _ = setup_book(tmp_path)
    record(sb, at("10:30:00"), price=.4)
    for now, bid, ask in [(at("10:31:00"), .5, .52), (at("10:32:00"), .4, .41)]:
        b = broker(now, bid, ask)
        await sb.update(b, now)
        b.get_option_quote.assert_awaited_once_with("IWM_TEST")
    sb.finish_session(at("10:32:30"))
    events = read_events(tmp_path / "events.jsonl")
    summary = summarize(events, DAY)
    assert summary["eligible_opportunities"] == 1
    assert summary["matched_exits"]["breakeven_25"]["difference"] == 0
    assert summary["matched_exits"]["partial_25_breakeven"]["difference"] == 30
    assert summary["current_permissions_replay"]["portfolio_pnl"] == 0
    assert summary["current_permissions_replay"]["admitted"] == 0
    assert summary["research_portfolios"]["partial_25_breakeven"]["portfolio_pnl"] == 30
    assert summary["research_portfolios"]["partial_25_breakeven"]["trades"][0]["quantity"] == 6
    assert to_markdown(summary).index("Eligible shadow evidence") < to_markdown(summary).index("Diagnostic setups")
    # A mismatched entry must not silently remain in the paired comparison.
    for event in events:
        if event.get("event") == "shadow_close" and event.get("channel") == "eligible" and event.get("variant") == "breakeven_25":
            event["entry_price"] += .01
    assert summarize(events, DAY)["matched_exits"]["breakeven_25"]["matched_pairs"] == 0


@pytest.mark.asyncio
async def test_stale_final_quote_leaves_portfolio_unresolved_and_progress_excluded(tmp_path):
    sb, _ = setup_book(tmp_path)
    record(sb, at("12:20:00"), price=1.82)
    await sb.update(broker(at("12:28:00"), 1.9, 1.91), at("12:28:00"))
    sb.finish_session(at("12:30:01"))
    # Candidate needs to have been inside the entry window for the replay test.
    events = read_events(tmp_path / "events.jsonl")
    for event in events:
        if event["event"] == "shadow_session_start":
            event["session_context"]["replay_policy"]["min_entry_minutes_before_eod"] = 5
            new_hash = policy_hash(event["session_context"]["replay_policy"])
            event["session_context"]["replay_policy_hash"] = new_hash
    for event in events:
        event["replay_policy_hash"] = new_hash
    report = summarize(events, DAY)
    replay = report["research_portfolios"]["baseline"]
    assert replay["status"] == "incomplete" and replay["unresolved"] == 1
    assert replay["portfolio_pnl"] is None
    assert report["matched_exits"]["breakeven_25"]["matched_pairs"] == 0
    assert report["observation_progress"]["completed_scheduled_sessions"] == 0


def stream(tmp_path, **policy_changes):
    c = context(config(tmp_path))
    c["replay_policy"].update(policy_changes)
    c["replay_policy_hash"] = policy_hash(c["replay_policy"])
    common = dict(model_version="4", session_id=c["runner_started_at"], replay_policy_hash=c["replay_policy_hash"])
    records = [{**common, "event": "shadow_session_start", "ts": c["runner_started_at"], "session_context": c}]

    def candidate(sid, hhmm, *, symbol=None, price=1, ask=None, strategy="orb", direction="short", qty=2):
        now, ask = at(hhmm), price if ask is None else ask
        records.append({**common, "event": "eligible_entry", "pair_id": sid, "ts": now.isoformat(),
                        "option_symbol": sid, "symbol": symbol or sid, "strategy_id": strategy,
                        "direction": direction, "entry_price": price, "quantity": qty,
                        "entry_time": now.isoformat(), "fill_deadline": (now + timedelta(seconds=120)).isoformat(),
                        "entry_filter_eligible": True,
                        "runtime_gates": {"reconciliation_clear": True, "kill_switch_clear": True},
                        "contract_metadata": {"bid": ask - .01, "ask": ask,
                                              "quote_timestamp": now.isoformat(), "quote_feed": "tradier_opra"}})

    def quote(sid, hhmm, bid, ask=None):
        now = at(hhmm)
        records.append({**common, "event": "shadow_quote", "ts": now.isoformat(), "option_symbol": sid,
                        "bid": bid, "ask": ask or bid + .01, "quote_timestamp": now.isoformat(), "quote_feed": "tradier_opra"})

    def end(hhmm="12:30:01"):
        records.append({**common, "event": "shadow_session_end", "ts": at(hhmm).isoformat(),
                        "api_errors": 0, "reconciliation_warnings": 0})

    return records, candidate, quote, end


def research(records, variant="baseline"):
    return replay_session(records, variant, respect_permissions=False)


def rejection(result, sid):
    return next(d["reasons"] for d in result["decisions"] if d["pair_id"] == sid and d["action"] == "rejected")


def test_pending_reserves_capacity_and_expiry_releases_slot_without_spending_entry(tmp_path):
    records, candidate, quote, end = stream(tmp_path)
    candidate("A", "10:00:00", ask=1.1)
    candidate("B", "10:00:30")
    quote("A", "10:02:01", .9, .91)  # late quote must not fill expired A
    candidate("C", "10:02:05")
    end("10:02:10")
    r = research(records)
    assert r["admitted"] == 2 and r["filled"] == 1 and r["closed"] == 1
    assert "max_active_positions" in rejection(r, "B")
    assert r["trades"][0]["pair_id"] == "C"
    assert any(d["action"] == "expired" and d["pair_id"] == "A" for d in r["decisions"])


def test_expired_submission_still_reserves_its_symbol_for_the_day(tmp_path):
    records, candidate, quote, end = stream(tmp_path)
    candidate("A", "10:00:00", symbol="IWM", ask=1.1)
    quote("A", "10:02:01", .9)
    candidate("B", "10:03:00", symbol="IWM")
    end()
    assert "symbol_already_submitted_today" in rejection(research(records), "B")


def test_orb_reservation_preserves_last_slot_in_research_portfolio(tmp_path):
    records, candidate, quote, end = stream(tmp_path)
    for i in range(2):
        candidate(str(i), f"10:{i * 2:02}:00", strategy="vwap_reclaim")
        quote(str(i), f"10:{i * 2 + 1:02}:00", 2.01)
    candidate("vwap", "10:04:00", strategy="vwap_reclaim")
    candidate("orb", "10:04:01")
    end("10:04:30")
    r = research(records)
    assert "orb_slot_reserved" in rejection(r, "vwap")
    assert r["filled"] == 3 and r["trades"][-1]["strategy_id"] == "orb"


def test_three_entry_daily_limit_is_not_released_when_a_trade_closes(tmp_path):
    records, candidate, quote, end = stream(tmp_path)
    for i in range(3):
        candidate(str(i), f"10:{i * 2:02}:00")
        quote(str(i), f"10:{i * 2 + 1:02}:00", 2.01)
    candidate("fourth", "10:06:00")
    end()
    r = research(records)
    assert r["filled"] == 3 and r["closed"] == 3
    assert "max_entries" in rejection(r, "fourth")


def test_cooldown_and_correlated_stop_lock_are_chronological(tmp_path):
    records, candidate, quote, end = stream(tmp_path)
    candidate("index", "10:00:00", symbol="IWM")
    quote("index", "10:01:00", .5)
    candidate("early", "10:02:00", symbol="MARA")
    candidate("correlated", "10:16:00", symbol="SPY")
    candidate("clear", "10:16:01", symbol="MARA")
    end("10:16:30")
    r = research(records)
    assert "cooldown_after_loss" in rejection(r, "early")
    assert "correlated_stop_lock" in rejection(r, "correlated")
    assert r["admitted"] == 2


@pytest.mark.parametrize("policy, expected", [
    ({"daily_loss_dollars": 100}, "experiment_daily_loss"),
    ({"max_losing_trades": 1}, "experiment_loss_count"),
    ({"max_daily_loss_fraction": .001}, "account_daily_loss"),
])
def test_loss_gates_block_after_cooldown_without_using_future_wins(tmp_path, policy, expected):
    records, candidate, quote, end = stream(tmp_path, **policy)
    candidate("loss", "10:00:00")
    quote("loss", "10:01:00", .5)
    candidate("later", "10:16:01")
    quote("later", "10:17:00", 3)  # cannot rescue a trade that was rejected
    end()
    r = research(records)
    assert expected in rejection(r, "later")
    assert r["portfolio_pnl"] == -100 and r["filled"] == 1


def test_partial_replay_recalculates_whole_contracts_after_equity_sizing(tmp_path):
    records, candidate, quote, end = stream(tmp_path)
    records[0]["session_context"]["starting_equity"] = 10_000
    candidate("A", "10:00:00", price=.4, qty=6)
    quote("A", "10:01:00", .5)
    quote("A", "10:02:00", .4)
    end()
    r = research(records, "partial_25_breakeven")
    assert r["trades"][0]["quantity"] == 2
    assert r["portfolio_pnl"] == 10  # one whole contract sold at .50, one at .40


@pytest.mark.parametrize("case,reason", [("budget", "premium_budget"), ("eod", "eod_entry_cutoff"),
                                        ("recon", "reconciliation_not_clear"), ("kill", "kill_switch_not_clear")])
def test_budget_session_and_runtime_gates_are_enforced(tmp_path, case, reason):
    records, candidate, quote, end = stream(tmp_path)
    candidate("A", "12:01:00" if case == "eod" else "10:00:00", qty=3 if case == "budget" else 2)
    if case == "recon":
        records[-1]["runtime_gates"]["reconciliation_clear"] = False
    if case == "kill":
        records[-1]["runtime_gates"]["kill_switch_clear"] = False
    end()
    assert reason in rejection(research(records), "A")


def test_historical_missing_quotes_or_policy_cannot_be_backfilled_from_close_pnl(tmp_path):
    old = [{"event": "shadow_close", "model_version": "3", "shadow_pnl": 79, "ts": at("11:00:00").isoformat()}]
    assert research(old)["status"] == "unavailable"
    records, candidate, quote, end = stream(tmp_path)
    candidate("A", "10:00:00")
    # A later profitable diagnostic close is never used as a replay outcome.
    records.append(dict(old[0], model_version="4"))
    end()
    assert research(records)["portfolio_pnl"] is None
    malformed = copy.deepcopy(records)
    malformed[0]["session_context"].pop("starting_equity")
    assert research(malformed)["status"] == "unavailable"


def test_progress_counts_complete_matching_observation_days_and_never_activates(tmp_path):
    records, candidate, quote, end = stream(tmp_path)
    end()
    c = records[0]["session_context"]
    progress = observation_progress(records, c, through_date=DAY)
    assert progress["completed_scheduled_sessions"] == 1
    assert progress["eligible_opportunities"] == 0
    assert not progress["review_checkpoint_reached"] and not progress["automatic_strategy_activation"]
    records.append(copy.deepcopy(records[0]))
    assert observation_progress(records, c, through_date=DAY)["completed_scheduled_sessions"] == 0


def test_observation_progress_does_not_pool_changed_entry_quality_rules(tmp_path):
    records, candidate, quote, end = stream(tmp_path)
    end()
    changed = config(tmp_path)
    changed.paper_scaled_min_signal_quality = 4
    progress = observation_progress(records, context(changed), through_date=DAY)
    assert progress["completed_scheduled_sessions"] == 0


@pytest.mark.parametrize("change", ["provider", "late", "early", "errors", "permissions", "model"])
def test_progress_excludes_mixed_controls_late_or_incomplete_sessions(tmp_path, change):
    records, candidate, quote, end = stream(tmp_path)
    end("11:00:00" if change == "early" else "12:30:01")
    c = copy.deepcopy(records[0]["session_context"])
    actual = records[0]["session_context"]
    if change == "provider":
        actual["options_data_provider"] = "alpaca"
    elif change == "late":
        actual["start_delay_seconds"] = 300
    elif change == "errors":
        records[-1]["api_errors"] = 1
    elif change == "permissions":
        actual["broker_entry_strategies"] = ["orb"]
    elif change == "model":
        actual["shadow_model_version"] = "3"
    assert observation_progress(records, c, through_date=DAY)["completed_scheduled_sessions"] == 0


@pytest.mark.asyncio
async def test_first_eligible_anchor_can_open_after_diagnostic_close_and_survives_restart(tmp_path):
    sb, c = setup_book(tmp_path)
    record(sb, at("10:35:24"), regime="neutral")
    await sb.update(broker(at("10:36:00"), .5, .51), at("10:36:00"))
    assert not sb._open
    record(sb, at("10:39:44"), signal_time=at("10:30:00"))
    resumed = ShadowBook(sb._settings, tmp_path / "events.jsonl", tmp_path / "state.json",
                         clock=lambda: at("10:39:45"), session_context=c)
    record(resumed, at("10:39:46"), signal_time=at("10:30:00"))
    events = read_events(tmp_path / "events.jsonl")
    assert len([r for r in events if r["event"] == "eligible_entry"]) == 1
    assert research(events)["status"] == "unavailable"  # restarted coverage needs review


@pytest.mark.asyncio
async def test_daily_report_persists_completed_shadow_evidence(tmp_path, monkeypatch):
    from app.evaluation import daily_report
    from app.evaluation.post_session import PostSessionResult, _build_and_save_report
    sb, c = setup_book(tmp_path)
    record(sb, at("10:30:00"), price=.4)
    await sb.update(broker(at("10:31:00"), .8, .81), at("10:31:00"))
    sb.finish_session(at("12:30:01"))
    (tmp_path / "shadow_book.jsonl").write_text((tmp_path / "events.jsonl").read_text())
    report = daily_report.DailyReport(DAY, c["runner_started_at"], at("12:30:01").isoformat(),
                                     session_context=c, shadow_model_version="4")
    monkeypatch.setattr(daily_report, "build_daily_report", AsyncMock(return_value=report))
    monkeypatch.setattr(daily_report, "send_summary_alert", AsyncMock())
    cfg = sb._settings
    cfg.paper_eval_permissive_entry_mode = False
    cfg.evaluation_output_dir = str(tmp_path)
    result = PostSessionResult(DAY)
    await _build_and_save_report(object(), cfg, DAY, None, result)
    saved = json.loads((tmp_path / "reports" / f"{DAY}.json").read_text())
    assert saved["shadow_evaluation"]["research_portfolios"]["baseline"]["portfolio_pnl"] == 240
    assert saved["shadow_evaluation"]["observation_progress"]["completed_scheduled_sessions"] == 1
    text = (tmp_path / "reports" / f"{DAY}.md").read_text()
    assert text.index("Eligible shadow evidence") < text.index("Diagnostic setups")


@pytest.mark.asyncio
async def test_shutdown_refreshes_once_per_contract_and_replay_uses_that_quote(tmp_path):
    sb, _ = setup_book(tmp_path)
    record(sb, at("10:30:31"), price=1.81)
    await sb.update(broker(at("12:29:44"), 1.45, 1.46), at("12:29:44"))
    final_broker = broker(at("12:30:02"), 1.48, 1.49)
    await sb.refresh_and_finish_session(final_broker, at("12:30:02"))
    final_broker.get_option_quote.assert_awaited_once_with("IWM_TEST")
    events = read_events(tmp_path / "events.jsonl")
    closes = [e for e in events if e["event"] == "shadow_close"]
    assert len(closes) == 4  # Diagnostic and eligible baseline/breakeven share one quote.
    assert all(e["exit_price"] == 1.48 and e["final_quote_refresh"] == "success" for e in closes)
    assert all(e["exit_quote_age_seconds"] == 0 for e in closes)
    summary = summarize(events, DAY)
    assert summary["research_portfolios"]["baseline"]["portfolio_pnl"] == -33
    assert summary["eligible_independent"]["baseline"]["sized_pnl"] == -33
    assert summary["observation_progress"]["completed_scheduled_sessions"] == 1
    assert "Eligible baseline exit quotes" in to_markdown(summary)
    assert events[-1]["final_quote_refresh_required"] is True
    assert sb.open_count() == 0


@pytest.mark.parametrize("issue", ["request_failure", "stale", "future", "crossed", "missing_time", "indicative"])
@pytest.mark.asyncio
async def test_failed_final_refresh_cannot_reuse_a_recent_cached_quote(tmp_path, issue):
    sb, _ = setup_book(tmp_path)
    record(sb, at("10:30:31"), price=1.81)
    # This cached quote would still pass the 60-second rule at shutdown.
    await sb.update(broker(at("12:29:44"), 1.45, 1.46), at("12:29:44"))
    final_broker = broker(at("12:30:02"), 1.48, 1.49)
    q = final_broker.get_option_quote.return_value
    if issue == "request_failure": final_broker.get_option_quote.side_effect = RuntimeError("unavailable")
    elif issue == "stale": q.timestamp = at("12:28:00")
    elif issue == "future": q.timestamp = at("12:30:03")
    elif issue == "crossed": q.bid = 1.5
    elif issue == "missing_time": q.timestamp = None
    elif issue == "indicative": q.feed = "indicative"
    await sb.refresh_and_finish_session(final_broker, at("12:30:02"))
    final_broker.get_option_quote.assert_awaited_once()
    events = read_events(tmp_path / "events.jsonl")
    closes = [e for e in events if e["event"] == "shadow_close"]
    assert all(e["shadow_pnl"] is None and not e["outcome_priced"] for e in closes)
    assert all(e["final_quote_refresh"] == "failed" for e in closes)
    summary = summarize(events, DAY)
    assert summary["research_portfolios"]["baseline"]["status"] == "incomplete"
    assert summary["research_portfolios"]["baseline"]["portfolio_pnl"] is None
    assert summary["observation_progress"]["completed_scheduled_sessions"] == 0
    assert "unpriced_eligible_exit" in summary["observation_progress"]["excluded_dates"][DAY]


@pytest.mark.parametrize("timeout_scope", ["request", "whole_refresh"])
@pytest.mark.asyncio
async def test_final_refresh_timeout_completes_as_unpriced(tmp_path, monkeypatch, timeout_scope):
    import asyncio
    from app.evaluation import shadow_book
    sb, _ = setup_book(tmp_path)
    record(sb, at("10:30:31"), price=1.81)
    cancelled = asyncio.Event()
    async def hanging_quote(symbol):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    attr = "_FINAL_QUOTE_TIMEOUT_SECONDS" if timeout_scope == "request" else "_FINAL_REFRESH_TIMEOUT_SECONDS"
    monkeypatch.setattr(shadow_book, attr, .01)
    source = SimpleNamespace(get_option_quote=AsyncMock(side_effect=hanging_quote))
    await sb.refresh_and_finish_session(source, at("12:30:02"))
    assert cancelled.is_set() and sb.open_count() == 0
    events = read_events(tmp_path / "events.jsonl")
    assert events[-1]["event"] == "shadow_session_end"
    assert summarize(events, DAY)["research_portfolios"]["baseline"]["portfolio_pnl"] is None


@pytest.mark.asyncio
async def test_shutdown_does_not_create_new_fills_for_pending_shadow_entries(tmp_path):
    sb, _ = setup_book(tmp_path)
    record(sb, at("10:30:00"), price=1.81, ask=1.9)
    source = broker(at("10:31:00"), 1.7, 1.71)
    await sb.refresh_and_finish_session(source, at("10:31:00"))
    source.get_option_quote.assert_not_awaited()
    closes = [e for e in read_events(tmp_path / "events.jsonl") if e["event"] == "shadow_close"]
    assert all(not e["fill_validated"] and e["shadow_pnl"] is None for e in closes)
    result = research(read_events(tmp_path / "events.jsonl"))
    assert result["filled"] == 0
    assert any(d["action"] == "cancelled_at_end" for d in result["decisions"])


def test_partial_scenario_without_two_contract_candidates_is_not_executable(tmp_path):
    records, candidate, quote, end = stream(tmp_path)
    candidate("IWM", "10:30:31", price=1.81, qty=1)
    quote("IWM", "12:29:44", 1.45)
    end()
    result = research(records, "partial_25_breakeven")
    assert result["status"] == "not_executable" and result["portfolio_pnl"] is None
    assert result["reason"] == "partial_exit_requires_two_contracts"
    assert result["realized_pnl"] == 0 and result["admitted"] == 0
    summary = summarize(records, DAY)
    assert "not executable | 0 | 0 | unavailable" in to_markdown(summary)
    assert summary["current_permissions_replay"]["portfolio_pnl"] == 0


@pytest.mark.asyncio
async def test_final_quote_for_held_diagnostic_cannot_fill_pending_eligible_replay(tmp_path):
    sb, _ = setup_book(tmp_path)
    record(sb, at("10:30:00"), price=1.9, quality=2)
    record(sb, at("10:30:30"), price=1.81, ask=1.9)
    source = broker(at("10:31:00"), 1.7, 1.71)
    await sb.refresh_and_finish_session(source, at("10:31:00"))
    source.get_option_quote.assert_awaited_once_with("IWM_TEST")
    events = read_events(tmp_path / "events.jsonl")
    assert any(e["event"] == "shadow_quote" and e["purpose"] == "session_end" for e in events)
    eligible = [e for e in events if e["event"] == "shadow_close" and e["channel"] == "eligible"]
    assert eligible and all(not e["fill_validated"] for e in eligible)
    result = research(events)
    assert result["filled"] == 0 and result["closed"] == 0
    assert any(d["action"] == "cancelled_at_end" for d in result["decisions"])


def test_partial_scenario_with_an_executable_candidate_keeps_its_portfolio_result(tmp_path):
    records, candidate, quote, end = stream(tmp_path)
    candidate("one", "10:30:00", price=1.81, qty=1)
    candidate("two", "10:31:00", price=1, qty=2)
    quote("two", "12:29:45", 1.1)
    end()
    result = research(records, "partial_25_breakeven")
    assert result["status"] == "complete" and result["portfolio_pnl"] == 20
    assert result["admitted"] == 1
