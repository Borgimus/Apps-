"""
Tests for two defensive fixes:
  Fix 1 — STANDBY guard: when all scanner candidates are rejected,
           block CLI fallback unless ALLOW_CLI_FALLBACK_WHEN_SCANNER_REJECTS=True
           AND max universe rvol passes the fallback_min_rvol gate.
  Fix 2 — Exit spread awareness: log warning when spread > max_spread_pct
           on exit; never block stop_loss / eod_exit.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

ET = ZoneInfo("America/New_York")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / minimal stubs
# ─────────────────────────────────────────────────────────────────────────────

def _make_settings(
    allow_fallback: bool = False,
    fallback_min_rvol: float = 0.20,
    max_spread_pct: float = 0.10,
    uni_mode: str = "dynamic",
):
    uni = MagicMock()
    uni.mode = uni_mode
    uni.max_symbols_per_scan = 10
    uni.max_active_symbols = 3
    uni.min_scan_score = 40.0
    uni.allow_cli_fallback_when_scanner_rejects = allow_fallback
    uni.fallback_min_rvol = fallback_min_rvol
    uni.groups_enabled = ""       # empty → use YAML enabled_groups
    uni.max_per_group = 15
    uni.max_total_symbols = 40

    risk = MagicMock()
    risk.max_spread_pct = max_spread_pct
    risk.min_underlying_price = 0.0
    risk.min_underlying_avg_volume = 0

    settings = MagicMock()
    settings.universe = uni
    settings.risk = risk
    return settings


@dataclass
class _FakeMetrics:
    rvol: float = 0.05


@dataclass
class _FakeCandidate:
    symbol: str
    score: float = 30.0
    signal_type: str = "LONG"
    is_rejected: bool = True
    rejected_reasons: List[str] = field(default_factory=lambda: ["low_volume_chop"])
    reason_codes: List[str] = field(default_factory=lambda: ["low_volume_chop"])
    metrics: _FakeMetrics = field(default_factory=_FakeMetrics)


def _make_all_rejected_candidates(rvol: float = 0.05) -> List[_FakeCandidate]:
    syms = ["SPY", "AAPL", "NVDA"]
    return [_FakeCandidate(symbol=s, metrics=_FakeMetrics(rvol=rvol)) for s in syms]


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1 — STANDBY guard tests
# ─────────────────────────────────────────────────────────────────────────────

class TestStandbyGuard:

    # Test 1: all candidates rejected + fallback=False → STANDBY (returns None)
    @pytest.mark.asyncio
    async def test_all_rejected_fallback_disabled_returns_none(self):
        """When every candidate is rejected and fallback is disabled, scan returns None."""
        from collections import OrderedDict
        from scripts.session_runner import _run_universe_scan

        candidates = _make_all_rejected_candidates(rvol=0.05)
        settings = _make_settings(allow_fallback=False)

        mock_loader = MagicMock()
        mock_loader.mode = "dynamic"
        mock_loader.enabled_groups_from_yaml = []
        mock_loader.get_symbols.return_value = ["SPY", "AAPL", "NVDA"]
        mock_loader.get_symbols_with_groups.return_value = OrderedDict(
            [("SPY", "test"), ("AAPL", "test"), ("NVDA", "test")]
        )

        mock_scanner = MagicMock()
        mock_scanner.scan = AsyncMock(return_value=[MagicMock()] * 3)

        mock_scorer = MagicMock()
        mock_scorer.score_all.return_value = candidates

        journal = MagicMock()
        journal.log_event = AsyncMock()
        journal.commit = AsyncMock()

        scan_store = {}

        with (
            patch("app.scanning.UniverseLoader", return_value=mock_loader),
            patch("app.scanning.YFinanceScanner", return_value=mock_scanner),
            patch("app.scanning.CandidateScorer", return_value=mock_scorer),
        ):
            result = await _run_universe_scan(
                settings=settings,
                broker=MagicMock(),
                journal=journal,
                session_date="2026-05-12",
                scan_store=scan_store,
            )

        assert result is None, "Expected STANDBY (None) when fallback disabled"
        assert scan_store.get("standby") is True
        assert scan_store.get("standby_reason") is not None
        journal.log_event.assert_called_once()
        call_kwargs = journal.log_event.call_args.kwargs
        assert call_kwargs["event"] == "standby"

    # Test 2: allow_cli_fallback_when_scanner_rejects defaults to False
    def test_universe_settings_fallback_default_is_false(self):
        """UniverseSettings.allow_cli_fallback_when_scanner_rejects must default to False."""
        from app.config.settings import UniverseSettings
        s = UniverseSettings()
        assert s.allow_cli_fallback_when_scanner_rejects is False

    # Test 3: fallback=True but max rvol < fallback_min_rvol → still STANDBY
    @pytest.mark.asyncio
    async def test_fallback_true_but_low_rvol_returns_none(self):
        """Even if fallback is enabled, if max rvol < threshold the system enters STANDBY."""
        from collections import OrderedDict
        from scripts.session_runner import _run_universe_scan

        # rvol=0.05 < fallback_min_rvol=0.20
        candidates = _make_all_rejected_candidates(rvol=0.05)
        settings = _make_settings(allow_fallback=True, fallback_min_rvol=0.20)

        mock_loader = MagicMock()
        mock_loader.mode = "dynamic"
        mock_loader.enabled_groups_from_yaml = []
        mock_loader.get_symbols.return_value = ["SPY", "AAPL", "NVDA"]
        mock_loader.get_symbols_with_groups.return_value = OrderedDict(
            [("SPY", "test"), ("AAPL", "test"), ("NVDA", "test")]
        )

        mock_scanner = MagicMock()
        mock_scanner.scan = AsyncMock(return_value=[MagicMock()] * 3)

        mock_scorer = MagicMock()
        mock_scorer.score_all.return_value = candidates

        scan_store = {}

        with (
            patch("app.scanning.UniverseLoader", return_value=mock_loader),
            patch("app.scanning.YFinanceScanner", return_value=mock_scanner),
            patch("app.scanning.CandidateScorer", return_value=mock_scorer),
        ):
            result = await _run_universe_scan(
                settings=settings,
                broker=MagicMock(),
                journal=None,
                session_date="2026-05-12",
                scan_store=scan_store,
            )

        assert result is None
        assert scan_store.get("standby") is True
        assert "rvol" in (scan_store.get("standby_reason") or "")

    # Test 4: fallback=True and max rvol >= threshold → fallback allowed (returns [])
    @pytest.mark.asyncio
    async def test_fallback_true_and_rvol_gate_passes_returns_empty_list(self):
        """When fallback enabled and rvol gate passes, scan returns [] (fallback allowed)."""
        from collections import OrderedDict
        from scripts.session_runner import _run_universe_scan

        # rvol=0.30 >= fallback_min_rvol=0.20
        candidates = _make_all_rejected_candidates(rvol=0.30)
        settings = _make_settings(allow_fallback=True, fallback_min_rvol=0.20)

        mock_loader = MagicMock()
        mock_loader.mode = "dynamic"
        mock_loader.enabled_groups_from_yaml = []
        mock_loader.get_symbols.return_value = ["SPY", "AAPL", "NVDA"]
        mock_loader.get_symbols_with_groups.return_value = OrderedDict(
            [("SPY", "test"), ("AAPL", "test"), ("NVDA", "test")]
        )

        mock_scanner = MagicMock()
        mock_scanner.scan = AsyncMock(return_value=[MagicMock()] * 3)

        mock_scorer = MagicMock()
        mock_scorer.score_all.return_value = candidates

        scan_store = {}

        with (
            patch("app.scanning.UniverseLoader", return_value=mock_loader),
            patch("app.scanning.YFinanceScanner", return_value=mock_scanner),
            patch("app.scanning.CandidateScorer", return_value=mock_scorer),
        ):
            result = await _run_universe_scan(
                settings=settings,
                broker=MagicMock(),
                journal=None,
                session_date="2026-05-12",
                scan_store=scan_store,
            )

        assert result == [], "Expected [] when fallback is allowed"
        assert scan_store.get("standby") is False

    # Test 5: dashboard session_state exposes scanner_standby=True from scan_store
    @pytest.mark.asyncio
    async def test_dashboard_session_state_exposes_standby_flag(self):
        """The /session/state endpoint returns scanner_standby from _scan_store."""
        import importlib
        import app.api.dashboard_api as dash_mod

        # Inject a standby state into the module-level _scan_store
        original = dict(dash_mod._scan_store)
        try:
            dash_mod._scan_store.clear()
            dash_mod._scan_store["standby"] = True
            dash_mod._scan_store["standby_reason"] = "all_3_candidates_rejected_fallback_disabled"

            # Build a minimal FastAPI test client
            from fastapi.testclient import TestClient

            app_instance = dash_mod.create_app(
                broker=MagicMock(),
                risk_manager=MagicMock(daily_pnl=0.0, trades_today=0, entries_today=0, pending_entries=0, exits_today=0, _session_date=None),
                position_manager=MagicMock(open_positions=MagicMock(return_value=[]), to_dict_list=MagicMock(return_value=[])),
                fill_tracker=MagicMock(count=MagicMock(return_value=0)),
                scan_results_store=dash_mod._scan_store,
            )

            with TestClient(app_instance) as client:
                resp = client.get("/session/state")

            assert resp.status_code == 200
            data = resp.json()
            assert data["scanner_standby"] is True
            assert data["standby_reason"] == "all_3_candidates_rejected_fallback_disabled"
        finally:
            dash_mod._scan_store.clear()
            dash_mod._scan_store.update(original)

    # Test 6: daily report includes standby_reason from session_logs
    @pytest.mark.asyncio
    async def test_daily_report_includes_standby_reason(self):
        """build_daily_report sets scanner_standby_activated and standby_reason from DB logs."""
        from app.evaluation.daily_report import build_daily_report

        standby_data = json.dumps({
            "reason": "all_3_candidates_rejected_fallback_disabled",
            "candidates_rejected": 3,
        })

        standby_log = MagicMock()
        standby_log.timestamp = datetime(2026, 5, 12, 9, 35, 0, tzinfo=ET)
        standby_log.level = "warning"
        standby_log.event = "standby"
        standby_log.message = "Scanner STANDBY: all_3_candidates_rejected_fallback_disabled"
        standby_log.data_json = standby_data

        db_session = MagicMock()
        # session logs → [standby_log], signals → [], trades → [], scan_rows → []
        db_session.execute = AsyncMock(
            side_effect=_make_db_execute_responses(
                logs=[standby_log], signals=[], trades=[], scan_rows=[]
            )
        )

        report = await build_daily_report(db_session, "2026-05-12")

        assert report.scanner_standby_activated is True
        assert report.standby_reason == "all_3_candidates_rejected_fallback_disabled"


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2 — Exit spread awareness tests
# ─────────────────────────────────────────────────────────────────────────────

class TestExitSpreadAwareness:

    # Test 7: exit spread warning logged and persisted when spread > max_spread_pct
    @pytest.mark.asyncio
    async def test_wide_spread_logs_and_persists_warning(self):
        """When exit spread exceeds max_spread_pct, a warning is logged and persisted."""
        from scripts.session_runner import monitor_positions

        pos = _make_position(entry_price=6.00)
        pm = _make_pm(pos, exit_reason="trailing_stop")
        broker = _make_broker(bid=3.00, ask=5.00)  # spread=(5-3)/4=0.50 >> 0.10
        journal = MagicMock()
        journal.log_event = AsyncMock()
        journal.commit = AsyncMock()
        journal.record_exit = AsyncMock()

        settings = _make_settings(max_spread_pct=0.10)

        await monitor_positions(
            broker=broker, pm=pm, journal=journal,
            risk=_make_risk(), now=datetime(2026, 5, 12, 10, 0, tzinfo=ET),
            dry_run=False, settings=settings,
        )

        spread_calls = [
            c for c in journal.log_event.call_args_list
            if c.kwargs.get("event") == "exit_spread_warning"
        ]
        assert len(spread_calls) == 1, "Expected one exit_spread_warning log event"
        data = spread_calls[0].kwargs["data"]
        assert data["spread_pct"] > 0.10
        assert data["is_emergency"] is False

    # Test 8: stop_loss exit proceeds despite wide spread (not blocked)
    @pytest.mark.asyncio
    async def test_stop_loss_proceeds_despite_wide_spread(self):
        """stop_loss exit is never blocked by wide spread — order is still placed."""
        from scripts.session_runner import monitor_positions

        pos = _make_position(entry_price=6.00)
        pm = _make_pm(pos, exit_reason="stop_loss")
        broker = _make_broker(bid=1.00, ask=9.00)  # extreme spread
        journal = MagicMock()
        journal.log_event = AsyncMock()
        journal.commit = AsyncMock()
        journal.record_exit = AsyncMock()

        settings = _make_settings(max_spread_pct=0.10)

        await monitor_positions(
            broker=broker, pm=pm, journal=journal,
            risk=_make_risk(), now=datetime(2026, 5, 12, 10, 0, tzinfo=ET),
            dry_run=False, settings=settings,
        )

        # Spread warning should be logged (is_emergency=True)
        spread_calls = [
            c for c in journal.log_event.call_args_list
            if c.kwargs.get("event") == "exit_spread_warning"
        ]
        assert len(spread_calls) == 1
        assert spread_calls[0].kwargs["data"]["is_emergency"] is True

        # But the exit order was still placed
        broker.place_option_order.assert_called_once()

    # Test 9: eod_exit proceeds with only a warning when spread is wide
    @pytest.mark.asyncio
    async def test_eod_exit_proceeds_despite_wide_spread(self):
        """eod_liquidate places exit order even when spread is wide (logs warning only)."""
        from scripts.session_runner import eod_liquidate

        pos = _make_position(entry_price=6.00)
        pm = MagicMock()
        pm.open_positions.return_value = [pos]
        pm.close = MagicMock()

        broker = _make_broker(bid=1.00, ask=9.00)
        journal = MagicMock()
        journal.log_event = AsyncMock()
        journal.commit = AsyncMock()
        journal.record_exit = AsyncMock()

        settings = _make_settings(max_spread_pct=0.10)

        await eod_liquidate(
            broker=broker, pm=pm, journal=journal,
            risk=_make_risk(), now=datetime(2026, 5, 12, 15, 45, tzinfo=ET),
            dry_run=False, settings=settings,
        )

        spread_calls = [
            c for c in journal.log_event.call_args_list
            if c.kwargs.get("event") == "exit_spread_warning"
        ]
        assert len(spread_calls) == 1
        assert spread_calls[0].kwargs["data"]["is_emergency"] is True

        # Exit order must still be placed
        broker.place_option_order.assert_called()


# ─────────────────────────────────────────────────────────────────────────────
# Shared test helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_position(entry_price: float = 6.00):
    from decimal import Decimal
    pos = MagicMock()
    pos.symbol = "MSFT"
    pos.option_symbol = "MSFT260513P00412500"
    pos.entry_price = entry_price
    pos.quantity = 1
    pos.strategy_id = "test_strat"
    pos.journal_id = 1
    pos.entry_time = datetime(2026, 5, 12, 9, 30, tzinfo=ET)
    return pos


def _make_pm(pos, exit_reason: str = "trailing_stop"):
    pm = MagicMock()
    pm.open_positions.return_value = [pos]
    pm.update_price = MagicMock()
    pm.should_exit.return_value = exit_reason
    pm.close = MagicMock()
    return pm


def _make_broker(bid: float = 3.50, ask: float = 4.00):
    from app.brokers.broker_interface import OptionQuote, OrderStatus
    quote = MagicMock()
    quote.bid = bid
    quote.ask = ask
    quote.mid = (bid + ask) / 2

    order_result = MagicMock()
    order_result.order_id = "test-order-001"
    order_result.status = OrderStatus.PENDING

    broker = MagicMock()
    broker.get_option_quote = AsyncMock(return_value=quote)
    broker.place_option_order = AsyncMock(return_value=order_result)
    return broker


def _make_risk():
    from decimal import Decimal
    risk = MagicMock()
    risk.record_trade = MagicMock()
    return risk


def _make_db_execute_responses(logs, signals, trades, scan_rows):
    """Build a side_effect list for db_session.execute that returns the right scalars each time."""
    call_count = 0

    class _FakeResult:
        def __init__(self, items):
            self._items = items

        def scalars(self):
            return self

        def all(self):
            return self._items

        def scalar(self):
            return len(self._items)

    responses = [
        _FakeResult(logs),    # session logs
        _FakeResult(signals), # DBSignal
        _FakeResult(trades),  # DBTradeJournal
        _FakeResult(scan_rows),  # DBScanResult
    ]

    async def _execute(*args, **kwargs):
        nonlocal call_count
        idx = min(call_count, len(responses) - 1)
        call_count += 1
        return responses[idx]

    return _execute
