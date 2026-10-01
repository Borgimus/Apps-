"""
Options-data provider health: the failure chain behind the 2026-09-22..29 outage.

Tradier rejected every request with 401. Each rejection was caught by a
per-call handler, candidates were dropped as ordinary rejections, the runner
fell back to SPY, the health report said api_errors=0 and data_feed_errors=0,
and six sessions were counted as completed observation. These tests pin each
link of that chain.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest

import scripts.session_runner as runner
from app.evaluation.evidence_annotations import outage_exclusion
from app.evaluation.shadow_summary import observation_progress
from app.operations.provider_health import (
    InstrumentedBroker,
    ProviderHealth,
    ProviderUnavailable,
    is_auth_error,
)

ET = ZoneInfo("America/New_York")


def _unauthorized():
    request = httpx.Request("GET", "https://api.tradier.com/v1/markets/quotes")
    response = httpx.Response(401, request=request)
    return httpx.HTTPStatusError("Client error '401 Unauthorized'", request=request, response=response)


def _chain(n=1):
    return SimpleNamespace(calls=[object()] * n, puts=[])


class _Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 29, 9, 35, tzinfo=ET)

    def __call__(self):
        return self.now


class TestMetering:

    @pytest.mark.asyncio
    async def test_calls_with_data_and_empty_payloads_are_counted_separately(self):
        raw = MagicMock()
        raw.get_option_chain = AsyncMock(side_effect=[_chain(), _chain(0)])
        health = ProviderHealth()
        broker = InstrumentedBroker(raw, health)
        await broker.get_option_chain("SPY", None)
        await broker.get_option_chain("SPY", None)
        assert health.requests["get_option_chain"] == 2
        assert health.successes["get_option_chain"] == 1
        assert health.empty["get_option_chain"] == 1

    @pytest.mark.asyncio
    async def test_unmetered_attributes_pass_through(self):
        raw = MagicMock()
        raw.get_account = AsyncMock(return_value="acct")
        raw.some_flag = 7
        broker = InstrumentedBroker(raw, ProviderHealth())
        assert await broker.get_account() == "acct"
        assert broker.some_flag == 7

    def test_auth_errors_are_recognised(self):
        assert is_auth_error(_unauthorized())
        assert is_auth_error(RuntimeError("Client error '403 Forbidden' for url"))
        assert not is_auth_error(RuntimeError("Server error '504 Gateway Timeout'"))


class TestAuthBreaker:

    @pytest.mark.asyncio
    async def test_repeated_401_stops_requests_to_the_provider(self):
        raw = MagicMock()
        raw.get_option_chain = AsyncMock(side_effect=_unauthorized())
        health = ProviderHealth(auth_breaker_threshold=3, clock=_Clock())
        broker = InstrumentedBroker(raw, health)
        for _ in range(3):
            with pytest.raises(httpx.HTTPStatusError):
                await broker.get_option_chain("SPY", None)
        assert health.breaker_open

        with pytest.raises(ProviderUnavailable):
            await broker.get_option_chain("SPY", None)
        assert raw.get_option_chain.await_count == 3, "no request after the breaker opens"
        assert health.short_circuited == 1

    @pytest.mark.asyncio
    async def test_probe_after_interval_and_success_closes_breaker(self):
        clock = _Clock()
        raw = MagicMock()
        raw.get_option_chain = AsyncMock(side_effect=[_unauthorized()] * 3 + [_chain()])
        health = ProviderHealth(auth_breaker_threshold=3, probe_interval_seconds=900, clock=clock)
        broker = InstrumentedBroker(raw, health)
        for _ in range(3):
            with pytest.raises(httpx.HTTPStatusError):
                await broker.get_option_chain("SPY", None)

        clock.now += timedelta(seconds=899)
        with pytest.raises(ProviderUnavailable):
            await broker.get_option_chain("SPY", None)

        clock.now += timedelta(seconds=1)
        assert await broker.get_option_chain("SPY", None)
        assert not health.breaker_open
        assert health.breaker_ever_opened

    @pytest.mark.asyncio
    async def test_retry_helper_does_not_retry_rejected_credentials(self, monkeypatch):
        sleep = AsyncMock()
        monkeypatch.setattr(runner.asyncio, "sleep", sleep)
        call = AsyncMock(side_effect=_unauthorized())
        with pytest.raises(httpx.HTTPStatusError):
            await runner._retry(call, label="chain(SPY)")
        assert call.await_count == 1
        sleep.assert_not_awaited()

        blocked = AsyncMock(side_effect=ProviderUnavailable("open"))
        with pytest.raises(ProviderUnavailable):
            await runner._retry(blocked, label="chain(SPY)")
        assert blocked.await_count == 1


class TestSessionVerdict:

    def test_outage_day_is_degraded(self):
        """Replays the 2026-09-22 pattern: every chain request returned 401."""
        health = ProviderHealth()
        for _ in range(3):
            health.record_failure("get_option_chain", _unauthorized())
        reasons = health.degraded_reasons()
        assert "options_provider_authorization_rejected" in reasons
        assert "no_options_chains_received" in reasons
        assert "options_failure_rate_above_threshold" in reasons

    def test_session_that_requested_nothing_is_degraded(self):
        assert ProviderHealth().degraded_reasons() == ["no_options_chain_requests"]

    def test_healthy_session_has_no_reasons(self):
        health = ProviderHealth()
        for _ in range(50):
            health.record_success("get_option_chain", _chain())
        health.record_failure("get_option_chain", RuntimeError("504 Gateway Timeout"))
        assert health.degraded_reasons() == []

    def test_runner_verdict_combines_options_and_market_data(self, monkeypatch):
        health = ProviderHealth()
        health.record_success("get_option_chain", _chain())
        monkeypatch.setattr(runner, "_PROVIDER_HEALTH", health)
        runner._reset_data_feed_errors()
        assert runner._data_health()["status"] == "ok"
        runner._record_data_feed_error("bars(SPY)")
        verdict = runner._data_health()
        runner._reset_data_feed_errors()
        assert verdict["status"] == "degraded"
        assert verdict["reasons"] == ["fetch_failures_after_retries"]


# ── Observation progress ─────────────────────────────────────────────────────

def _context(day, provider="tradier"):
    return {
        "options_data_provider": provider, "options_data_adapter_hash": "567d1f77",
        "evaluation_cohort": "cohort", "shadow_model_version": "4",
        "replay_policy_hash": "hash", "broker_entry_strategies": [],
        "start_delay_seconds": 10, "runner_started_at": f"{day}T09:30:10-04:00",
        "replay_policy": {"eod_exit_time": "12:30"},
        "observation_review_targets": {"scheduled_sessions": 5, "eligible_opportunities": 10},
    }


def _session(day, provider="tradier", **end_fields):
    ctx = _context(day, provider)
    sid = ctx["runner_started_at"]
    return ctx, [
        {"event": "shadow_session_start", "ts": sid, "session_id": sid, "session_context": ctx},
        {"event": "shadow_session_end", "ts": f"{day}T12:30:05-04:00", "session_id": sid,
         "api_errors": 0, "reconciliation_warnings": 0, **end_fields},
    ]


class TestObservationProgress:

    def test_degraded_session_is_excluded_with_its_reasons(self):
        ctx, events = _session("2026-10-01", provider="alpaca",
                               data_health_reasons=["no_options_chains_received"])
        progress = observation_progress(events, ctx, through_date="2026-10-01")
        assert progress["completed_scheduled_sessions"] == 0
        assert progress["excluded_dates"]["2026-10-01"] == ["data_health:no_options_chains_received"]

    def test_healthy_session_still_counts(self):
        ctx, events = _session("2026-10-01", provider="alpaca", data_health_reasons=[])
        progress = observation_progress(events, ctx, through_date="2026-10-01")
        assert progress["completed_scheduled_sessions"] == 1

    @pytest.mark.parametrize("day", ["2026-09-22", "2026-09-25", "2026-09-29", "2026-10-05"])
    def test_tradier_sessions_after_deactivation_are_excluded(self, day):
        ctx, events = _session(day)  # recorded before data_health existed
        progress = observation_progress(events, ctx, through_date=day)
        assert progress["completed_scheduled_sessions"] == 0
        assert "options_provider_deactivated" in progress["excluded_dates"][day]

    def test_tradier_session_before_deactivation_is_unaffected(self):
        assert outage_exclusion(_context("2026-09-21")) is None
        ctx, events = _session("2026-09-21")
        assert observation_progress(events, ctx, through_date="2026-09-21")[
            "completed_scheduled_sessions"] == 1


# ── Scanner fallback policy ──────────────────────────────────────────────────

@dataclass
class _Metrics:
    rvol: float = 2.0
    earnings_status: str = "clear"


@dataclass
class _Candidate:
    symbol: str
    score: float = 80.0
    signal_type: str = "LONG"
    is_rejected: bool = False
    rejected_reasons: List[str] = field(default_factory=list)
    reason_codes: List[str] = field(default_factory=list)
    universe_group: str = "test"
    metrics: _Metrics = field(default_factory=_Metrics)


def _scan_settings(allow_fallback):
    settings = MagicMock()
    settings.universe.mode = "dynamic"
    settings.universe.max_symbols_per_scan = 10
    settings.universe.max_active_symbols = 3
    settings.universe.min_scan_score = 40.0
    settings.universe.allow_cli_fallback_when_scanner_rejects = allow_fallback
    settings.universe.fallback_min_rvol = 0.2
    settings.universe.groups_enabled = ""
    settings.universe.max_per_group = 15
    settings.universe.max_total_symbols = 40
    settings.risk.min_underlying_price = 0.0
    settings.risk.min_underlying_avg_volume = 0
    return settings


async def _scan_with_no_confirmations(allow_fallback):
    loader = MagicMock()
    loader.mode = "dynamic"
    loader.enabled_groups_from_yaml = []
    loader.get_symbols_with_groups.return_value = OrderedDict([("SPY", "t"), ("IWM", "t")])
    scanner = MagicMock()
    scanner.scan = AsyncMock(return_value=[MagicMock(), MagicMock()])
    scanner.batch_fetch_failures = 0
    scorer = MagicMock()
    scorer.score_all.return_value = [_Candidate("SPY"), _Candidate("IWM")]
    confirmer = MagicMock()
    confirmer.confirm_all = AsyncMock(return_value=[])  # every chain fetch failed
    journal = MagicMock()
    journal.log_event = AsyncMock()
    journal.commit = AsyncMock()
    store = {}
    with (
        patch("app.scanning.UniverseLoader", return_value=loader),
        patch("app.scanning.YFinanceScanner", return_value=scanner),
        patch("app.scanning.CandidateScorer", return_value=scorer),
        patch("app.scanning.AlpacaConfirmer", return_value=confirmer),
    ):
        result = await runner._run_universe_scan(
            settings=_scan_settings(allow_fallback), broker=MagicMock(), journal=journal,
            session_date="2026-09-29", scan_store=store,
        )
    return result, store, journal


class TestScannerFallbackPolicy:

    @pytest.mark.asyncio
    async def test_unconfirmed_candidates_enter_standby_when_fallback_disabled(self):
        result, store, journal = await _scan_with_no_confirmations(allow_fallback=False)
        assert result is None, "must not hand the caller a licence to fall back to SPY"
        assert store["standby"] is True
        assert store["standby_reason"] == "no_options_confirmation_for_2_passing_candidates"
        events = [c.kwargs["event"] for c in journal.log_event.call_args_list]
        assert "standby" in events

    @pytest.mark.asyncio
    async def test_unconfirmed_candidates_fall_back_only_when_allowed(self):
        result, store, _ = await _scan_with_no_confirmations(allow_fallback=True)
        assert result == []
        assert store["standby"] is False
