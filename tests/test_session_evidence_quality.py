"""Source-data readiness and rejection counts, independent of DB log freshness."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from app.evaluation.pre_session import _check_data_feed_freshness, all_required_pass, format_check_table
from app.evaluation.shadow_funnel import rejection_breakdown

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 10, 10, 0, 10, tzinfo=ET)


def market_source(now=NOW):
    return SimpleNamespace(
        get_quote=AsyncMock(return_value=SimpleNamespace(bid=100, ask=100.01, timestamp=now-timedelta(seconds=2))),
        get_stock_bars=AsyncMock(return_value=[(now.replace(hour=9, minute=55, second=0), 100.0)]),
    )


@pytest.mark.asyncio
async def test_readiness_uses_quote_and_completed_regular_session_bars():
    source = market_source()
    # A still-forming bar must not be treated as a completed observation.
    source.get_stock_bars.return_value.append((NOW.replace(second=0), 100.1))
    check = await _check_data_feed_freshness(source, now=NOW)
    assert check.passed and check.status == "fresh" and not check.required
    source.get_quote.assert_awaited_once_with("SPY")
    assert "quote age=2.0s" in check.message and "bar close age=10.0s" in check.message
    assert source.get_stock_bars.await_args.kwargs["timeframe"] == "5Min"


@pytest.mark.parametrize("time", [(9, 20), (9, 30), (9, 34)])
@pytest.mark.asyncio
async def test_opening_warmup_is_explicit_and_does_not_block_scheduled_start(time):
    now = NOW.replace(hour=time[0], minute=time[1])
    source = market_source(now)
    check = await _check_data_feed_freshness(source, now=now)
    assert not check.passed and check.status == "warming_up"
    assert all_required_pass([check])
    assert "09:35" in check.message and "WARMING_UP" in format_check_table([check])
    source.get_stock_bars.assert_not_awaited()


@pytest.mark.parametrize("issue", ["stale_quote", "future_quote", "missing_quote_time", "crossed_quote",
                                  "nan_quote", "no_bars", "stale_bars", "future_bar", "naive_bar",
                                  "only_forming_bar", "prior_day_bar"])
@pytest.mark.asyncio
async def test_invalid_market_evidence_never_passes_readiness(issue):
    source = market_source()
    q = source.get_quote.return_value
    if issue == "stale_quote": q.timestamp = NOW - timedelta(seconds=61)
    elif issue == "future_quote": q.timestamp = NOW + timedelta(seconds=1)
    elif issue == "missing_quote_time": q.timestamp = None
    elif issue == "crossed_quote": q.bid = q.ask + 1
    elif issue == "nan_quote": q.bid = float("nan")
    elif issue == "no_bars": source.get_stock_bars.return_value = []
    elif issue == "stale_bars": source.get_stock_bars.return_value = [(NOW.replace(minute=40, hour=9), 100)]
    elif issue == "future_bar": source.get_stock_bars.return_value.append((NOW + timedelta(minutes=1), 100))
    elif issue == "naive_bar": source.get_stock_bars.return_value = [(NOW.replace(tzinfo=None), 100)]
    elif issue == "only_forming_bar": source.get_stock_bars.return_value = [(NOW.replace(second=0), 100)]
    elif issue == "prior_day_bar": source.get_stock_bars.return_value = [(NOW-timedelta(days=1), 100)]
    check = await _check_data_feed_freshness(source, now=NOW)
    assert not check.passed and check.status == "unverified"
    assert all_required_pass([check])  # Advisory status does not alter entry gates.


@pytest.mark.asyncio
async def test_readiness_request_failure_does_not_claim_a_fresh_feed():
    source = market_source()
    source.get_stock_bars.side_effect = RuntimeError("example server error")
    check = await _check_data_feed_freshness(source, now=NOW)
    assert not check.passed and check.status == "unavailable"
    assert "RuntimeError" in check.message


@pytest.mark.asyncio
async def test_malformed_session_hours_produce_an_unavailable_advisory():
    check = await _check_data_feed_freshness(market_source(), SimpleNamespace(market_open=None), now=NOW)
    assert not check.passed and check.status == "unavailable" and not check.required


@pytest.mark.asyncio
async def test_readiness_timeout_cancels_request(monkeypatch):
    from app.evaluation import pre_session
    cancelled = asyncio.Event()
    async def hanging_request(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    source = market_source()
    source.get_quote.side_effect = hanging_request
    monkeypatch.setattr(pre_session, "_DATA_CHECK_TIMEOUT_SECONDS", .01)
    check = await _check_data_feed_freshness(source, now=NOW)
    assert check.status == "unavailable" and "TimeoutError" in check.message
    assert cancelled.is_set()


def signal(opportunity="A", **values):
    return {"event": "signal", "session_id": "one", "opportunity_id": opportunity,
            "variant": "baseline", "strategy_id": "orb", "symbol": "IWM", "direction": "long",
            "ts": NOW.isoformat(), "entry_filter_eligible": False,
            "entry_filter_reasons": ["signal_quality_below_min", "premium_budget_or_price_invalid"],
            **values}


def test_funnel_deduplicates_repeats_excludes_variants_and_keeps_later_eligibility():
    events = [signal() for _ in range(20)]
    events += [signal(entry_filter_eligible=True, entry_filter_reasons=[]),
               {"event": "eligible_entry", "session_id": "one", "opportunity_id": "A"},
               signal("inverse", variant="inverted"), signal("be", variant="breakeven_25"),
               signal("B", entry_filter_reasons=["market_regime_mismatch", "market_regime_mismatch"])]
    result = rejection_breakdown(events)
    assert result["signal_observations"] == 22 and result["unique_setups"] == 2
    assert result["initially_rejected"] == 2 and result["initial_multiple_blockers"] == 1
    assert result["initial_rejections_by_reason"] == {
        "market_regime_mismatch": 1, "premium_budget_or_price_invalid": 1, "signal_quality_below_min": 1}
    assert result["ever_passed_entry_filters"] == result["became_eligible_after_rejection"] == 1
    assert result["eligible_anchors"] == 1
    assert sum(c["setups"] for c in result["initial_rejection_combinations"]) == 2


def test_funnel_preserves_session_identity_and_unknown_historical_eligibility():
    unknown = signal()
    unknown.pop("entry_filter_eligible")
    result = rejection_breakdown([unknown, signal(session_id="two"), signal(opportunity=None)])
    assert result["unique_setups"] == 2 and result["initial_eligibility_unrecorded"] == 1
    assert result["initially_rejected"] == 1 and result["observations_without_setup_id"] == 1
    assert result["by_strategy"]["orb"]["unique_setups"] == 2


def test_filter_pass_alone_does_not_invent_an_eligible_anchor():
    result = rejection_breakdown([signal(entry_filter_eligible=True, entry_filter_reasons=[])])
    assert result["ever_passed_entry_filters"] == 1 and result["eligible_anchors"] == 0
    assert rejection_breakdown([])["status"] == "unavailable"
