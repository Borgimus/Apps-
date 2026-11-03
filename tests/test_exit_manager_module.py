"""Exit management lives in app.trading.exit_manager; the runner delegates."""

from __future__ import annotations

import inspect

import pytest

import scripts.session_runner as runner
from app.trading import exit_manager


def test_failure_recorder_is_required_not_defaulted():
    """A default no-op recorder would silently drop fetch failures again."""
    param = inspect.signature(exit_manager.monitor_positions).parameters["record_failure"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


@pytest.mark.asyncio
async def test_runner_wrapper_injects_its_recorder_and_notifier(monkeypatch):
    seen = {}

    async def fake(*args, **kwargs):
        seen.update(kwargs)
        return 0

    sentinel = object()
    monkeypatch.setattr(exit_manager, "monitor_positions", fake)
    monkeypatch.setattr(runner, "_PUSH_NOTIFIER", sentinel)
    await runner.monitor_positions(broker=None, pm=None, journal=None, risk=None, now=None, dry_run=True)
    assert seen["record_failure"] is runner._record_data_feed_error
    assert seen["notifier"] is sentinel


def test_runner_keeps_the_shared_exit_constants():
    assert runner._MANDATORY_EXIT_REASONS is exit_manager.MANDATORY_EXIT_REASONS
    assert "quote_unavailable" in runner._MANDATORY_EXIT_REASONS
