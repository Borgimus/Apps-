"""A session that is alive but receiving no options data must alert mid-session,
and preflight must exercise the options provider before arming."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.operations import monitor as m
from app.utils.push_notifier import PushNotifier

ET = ZoneInfo("America/New_York")


def _heartbeat(now, data_health):
    snap = {"ts": now.isoformat(), "session_date": str(now.date()), "cycle": 40}
    if data_health is not None:
        snap["data_health"] = data_health
    return snap


def _write(root: Path, snapshot):
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "logs/live_status.json").write_text(json.dumps(snapshot))


HEALTHY = {"options_requests": 120, "chains_with_data": 18,
           "authorization_failures": 0, "breaker_open": False}
OUTAGE = {"options_requests": 3, "chains_with_data": 0,
          "authorization_failures": 3, "breaker_open": True}


class TestDataProblem:

    def test_rejected_authorization_is_a_problem_immediately(self):
        now = datetime(2026, 9, 22, 9, 40, tzinfo=ET)
        assert "authorization" in m.data_problem(_heartbeat(now, OUTAGE), now)

    def test_no_chains_is_tolerated_before_the_opening_scan_completes(self):
        now = datetime(2026, 9, 22, 9, 50, tzinfo=ET)
        quiet = dict(HEALTHY, chains_with_data=0)
        assert m.data_problem(_heartbeat(now, quiet), now) is None
        later = datetime(2026, 9, 22, 10, 5, tzinfo=ET)
        assert "No options chains" in m.data_problem(_heartbeat(later, quiet), later)

    def test_healthy_and_legacy_heartbeats_are_not_problems(self):
        now = datetime(2026, 9, 22, 11, 0, tzinfo=ET)
        assert m.data_problem(_heartbeat(now, HEALTHY), now) is None
        assert m.data_problem(_heartbeat(now, None), now) is None


class TestWatchdog:

    def test_live_session_with_dead_provider_alerts_once(self, tmp_path):
        """2026-09-22: heartbeats were fresh all morning while every options
        request returned 401. Liveness alone reported it healthy."""
        now = datetime(2026, 9, 22, 10, 30, tzinfo=ET)
        _write(tmp_path, _heartbeat(now, OUTAGE))
        sent = []
        assert not m.watchdog(tmp_path, now=now, query=lambda: True, sender=sent.append)
        assert not m.watchdog(tmp_path, now=now, query=lambda: True, sender=sent.append)
        assert len(sent) == 1 and "rejecting authorization" in sent[0]

    def test_live_productive_session_does_not_alert(self, tmp_path):
        now = datetime(2026, 9, 22, 10, 30, tzinfo=ET)
        _write(tmp_path, _heartbeat(now, HEALTHY))
        sent = []
        assert m.watchdog(tmp_path, now=now, query=lambda: True, sender=sent.append)
        assert sent == []


def test_heartbeat_carries_data_health(tmp_path):
    notifier = PushNotifier(log_dir=tmp_path)
    now = datetime(2026, 9, 30, 10, 0, tzinfo=ET)
    notifier.on_cycle(cycle=1, now=now, positions=0, entries_today=0, daily_pnl=0.0,
                      unrealized_pnl=0.0, active_symbols=[], pending_orders=0,
                      scanner_standby=False, session_date="2026-09-30", data_health=HEALTHY)
    status = json.loads((tmp_path / "live_status.json").read_text())
    assert status["data_health"] == HEALTHY


class TestOptionsDataCanary:

    def _patch(self, monkeypatch, broker, feed="opra"):
        settings = SimpleNamespace(live_trading_enabled=False, options_data_provider="alpaca",
                                   alpaca_options_feed=feed)
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        monkeypatch.setattr("app.brokers.factory.get_broker", lambda s: broker)

    @pytest.mark.asyncio
    async def test_rejected_credentials_fail_the_canary(self, monkeypatch):
        broker = MagicMock()
        broker.get_available_expirations = AsyncMock(
            side_effect=RuntimeError("Client error '401 Unauthorized'"))
        broker.close = AsyncMock()
        self._patch(monkeypatch, broker)
        with pytest.raises(RuntimeError, match="401"):
            await m.options_data_canary()
        broker.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_working_provider_reports_contract_count(self, monkeypatch):
        today = datetime.now(ET).date()
        broker = MagicMock()
        broker.get_available_expirations = AsyncMock(return_value=[today])
        broker.get_option_chain = AsyncMock(
            return_value=SimpleNamespace(calls=[1, 2], puts=[3]))
        broker.close = AsyncMock()
        self._patch(monkeypatch, broker)
        result = await m.options_data_canary()
        assert result["contracts"] == 3 and result["expiration"] == str(today)

    @pytest.mark.asyncio
    async def test_no_upcoming_expirations_fails(self, monkeypatch):
        broker = MagicMock()
        broker.get_available_expirations = AsyncMock(return_value=[])
        broker.close = AsyncMock()
        self._patch(monkeypatch, broker)
        with pytest.raises(ValueError, match="No upcoming option expirations"):
            await m.options_data_canary()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("feed", [None, "indicative"])
    async def test_alpaca_provider_requires_the_opra_feed(self, monkeypatch, feed):
        broker = MagicMock()
        broker.get_available_expirations = AsyncMock()
        self._patch(monkeypatch, broker, feed=feed)
        with pytest.raises(ValueError, match="opra"):
            await m.options_data_canary()
        broker.get_available_expirations.assert_not_awaited()
