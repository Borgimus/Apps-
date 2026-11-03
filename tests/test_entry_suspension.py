"""Broker entries stay suspended, and no flag can make rsi_trend tradeable."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import scripts.session_runner as runner
from app.config import Settings
from app.evaluation.session_context import capture_session_context

ET = ZoneInfo("America/New_York")
STARTED = datetime(2026, 9, 30, 9, 30, 10, tzinfo=ET)
STRATEGIES = ("orb", "vwap_reclaim", "rsi_trend")


def _settings(tmp_path, **overrides):
    values = dict(_env_file=None, broker="alpaca", live_trading_enabled=False,
                  paper_evaluation_mode=True, paper_eval_permissive_entry_mode=True,
                  paper_scaled_sizing_enabled=True, paper_scaled_guardrails_enabled=True,
                  paper_scaled_shadow_only_strategies="orb,vwap_reclaim",
                  kill_switch_file=str(tmp_path / "KILL_SWITCH"))
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize("permissive", [True, False])
def test_current_configuration_permits_no_broker_entries(tmp_path, permissive):
    ctx = capture_session_context(
        _settings(tmp_path, paper_eval_permissive_entry_mode=permissive), STARTED, STRATEGIES)
    assert ctx["broker_entry_strategies"] == []
    assert "rsi_trend" in ctx["diagnostic_only_strategies"]


def test_runner_never_passes_rsi_trend_to_order_placement():
    signals = [SimpleNamespace(strategy_id=s) for s in STRATEGIES]
    assert [s.strategy_id for s in runner._exclude_diagnostic_only(signals)] == ["orb", "vwap_reclaim"]
