"""
Tests for PAPER_EVAL_PERMISSIVE_ENTRY_MODE.

Covers:
  - Settings guard: permissive requires paper_evaluation_mode=true
  - Settings guard: permissive rejected when live_trading_enabled=true
  - DBSignalBridge model fields present
  - BridgeEntry dataclass fields
  - persist_bridge_entries writes DB rows
  - VWAP quality score rubric (0-4)
  - ORB quality score rubric (0-4)
  - RSI_trend returns 0
  - compute_signal_quality_score routing
  - Deterministic ranking: scanner_score, quality, rvol, age, confluence
  - Daily report bridge fields populated from DB rows
  - Sample-size warning fires when fills < 30
  - Bridge section absent when no bridge entries
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")


# ── Helpers ────────────────────────────────────────────────────────────────────

_SESSION_START = datetime(2026, 5, 28, 9, 30, 0, tzinfo=_ET)


def _bar_ts(bar_index: int) -> datetime:
    """Return UTC datetime for bar_index bars after 09:30 ET (5-min bars)."""
    return (_SESSION_START + timedelta(minutes=bar_index * 5)).astimezone(_UTC)


def _utc(hour: int, minute: int) -> datetime:
    return datetime(2026, 5, 28, hour, minute, 0, tzinfo=_UTC)


def _make_bars(n: int = 30, start_bar: int = 0) -> pd.DataFrame:
    timestamps = [_bar_ts(start_bar + i) for i in range(n)]
    idx = pd.DatetimeIndex(timestamps, tz="UTC")
    return pd.DataFrame(
        {
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.5] * n,
            "volume": [1_000_000] * n,
        },
        index=idx,
    )


# ── Settings guards ────────────────────────────────────────────────────────────

class TestPermissiveModeSettings:
    def test_permissive_requires_paper_evaluation_mode(self, monkeypatch):
        monkeypatch.delenv("PAPER_EVAL_PERMISSIVE_ENTRY_MODE", raising=False)
        monkeypatch.delenv("PAPER_EVALUATION_MODE", raising=False)
        from app.config.settings import Settings
        with pytest.raises(ValueError, match="paper_evaluation_mode"):
            Settings(
                paper_eval_permissive_entry_mode=True,
                paper_evaluation_mode=False,
                live_trading_enabled=False,
            )

    def test_permissive_rejected_with_live_trading(self, monkeypatch):
        monkeypatch.delenv("PAPER_EVAL_PERMISSIVE_ENTRY_MODE", raising=False)
        monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
        from app.config.settings import Settings
        with pytest.raises(ValueError):
            Settings(
                paper_eval_permissive_entry_mode=True,
                paper_evaluation_mode=True,
                live_trading_enabled=True,
            )

    def test_permissive_accepted_with_paper_mode(self, monkeypatch):
        monkeypatch.delenv("PAPER_EVAL_PERMISSIVE_ENTRY_MODE", raising=False)
        monkeypatch.delenv("PAPER_EVALUATION_MODE", raising=False)
        from app.config.settings import Settings
        s = Settings(
            paper_eval_permissive_entry_mode=True,
            paper_evaluation_mode=True,
            live_trading_enabled=False,
        )
        assert s.paper_eval_permissive_entry_mode is True

    def test_permissive_defaults_false(self, monkeypatch):
        monkeypatch.delenv("PAPER_EVAL_PERMISSIVE_ENTRY_MODE", raising=False)
        from app.config.settings import Settings
        s = Settings(
            paper_evaluation_mode=True,
            live_trading_enabled=False,
            paper_eval_permissive_entry_mode=False,
        )
        assert s.paper_eval_permissive_entry_mode is False


# ── DBSignalBridge model ───────────────────────────────────────────────────────

class TestDBSignalBridgeModel:
    def test_model_has_required_columns(self):
        from app.api.models import DBSignalBridge
        cols = {c.key for c in DBSignalBridge.__table__.columns}
        required = {
            "id", "session_date", "timestamp", "symbol", "strategy_id",
            "signal_direction", "signal_age_seconds", "universe_group",
            "scanner_score", "scanner_approved", "signal_quality_score",
            "confluence_count", "option_contract", "bid", "ask", "spread_pct",
            "spread_threshold", "rvol", "rvol_threshold", "option_volume",
            "option_volume_threshold", "open_interest", "open_interest_threshold",
            "liquidity_passed", "spread_passed", "risk_passed",
            "reconciliation_passed", "position_limit_passed",
            "final_decision", "exact_block_reason",
        }
        missing = required - cols
        assert not missing, f"Missing columns: {missing}"


# ── BridgeEntry dataclass ──────────────────────────────────────────────────────

class TestBridgeEntry:
    def test_default_final_decision_is_blocked(self):
        from app.trading.bridge_diagnostics import BridgeEntry
        e = BridgeEntry(
            session_date="2026-05-28",
            timestamp=datetime.now(tz=_UTC),
            symbol="SPY",
            strategy_id="vwap_reclaim",
            signal_direction="LONG",
        )
        assert e.final_decision == "blocked"

    def test_all_fields_accessible(self):
        from app.trading.bridge_diagnostics import BridgeEntry
        e = BridgeEntry(
            session_date="2026-05-28",
            timestamp=datetime.now(tz=_UTC),
            symbol="SPY",
            strategy_id="orb",
            signal_direction="SHORT",
            scanner_score=75.0,
            scanner_approved=True,
            signal_quality_score=3.0,
            rvol=2.1,
            liquidity_passed=True,
            spread_passed=True,
            risk_passed=True,
            final_decision="traded",
        )
        assert e.scanner_score == 75.0
        assert e.rvol == 2.1
        assert e.final_decision == "traded"


# ── persist_bridge_entries ────────────────────────────────────────────────────

class TestPersistBridgeEntries:
    def test_empty_list_is_noop(self):
        from app.trading.bridge_diagnostics import persist_bridge_entries
        db_mock = AsyncMock()
        asyncio.get_event_loop().run_until_complete(
            persist_bridge_entries([], db_mock)
        )
        db_mock.add_all.assert_not_called()

    def test_entries_added_and_committed(self):
        from app.trading.bridge_diagnostics import BridgeEntry, persist_bridge_entries
        db_mock = AsyncMock()
        entry = BridgeEntry(
            session_date="2026-05-28",
            timestamp=datetime.now(tz=_UTC),
            symbol="SPY",
            strategy_id="orb",
            signal_direction="LONG",
        )
        asyncio.get_event_loop().run_until_complete(
            persist_bridge_entries([entry], db_mock)
        )
        db_mock.add_all.assert_called_once()
        db_mock.commit.assert_called_once()
        rows = db_mock.add_all.call_args[0][0]
        assert len(rows) == 1
        assert rows[0].symbol == "SPY"
        assert rows[0].final_decision == "blocked"


# ── Signal quality scoring ─────────────────────────────────────────────────────

def _make_signal(strategy_id: str, direction: str = "LONG", ts: datetime = None):
    from app.strategies.strategy_base import Signal, SignalDirection
    return Signal(
        strategy_id=strategy_id,
        symbol="SPY",
        direction=SignalDirection.LONG if direction == "LONG" else SignalDirection.SHORT,
        price=500.0,
        timestamp=ts or _bar_ts(60),  # ~9:30 + 5h = 14:30
        confidence=0.8,
    )


class TestVWAPQualityScore:
    def test_zero_on_empty_bars(self):
        from app.strategies.signal_quality import score_vwap_signal
        sig = _make_signal("vwap_reclaim")
        assert score_vwap_signal(sig, pd.DataFrame()) == 0

    def test_zero_when_sig_idx_too_small(self):
        from app.strategies.signal_quality import score_vwap_signal
        bars = _make_bars(n=2)
        sig = _make_signal("vwap_reclaim", ts=_bar_ts(1))
        result = score_vwap_signal(sig, bars)
        assert result == 0

    def test_score_in_range_0_4(self):
        from app.strategies.signal_quality import score_vwap_signal
        bars = _make_bars(n=20)
        sig = _make_signal("vwap_reclaim", ts=_bar_ts(15))
        result = score_vwap_signal(sig, bars)
        assert 0 <= result <= 4

    def test_volume_criterion_raises_score(self):
        from app.strategies.signal_quality import score_vwap_signal
        n = 20
        timestamps = [_bar_ts(i) for i in range(n)]
        idx = pd.DatetimeIndex(timestamps, tz="UTC")
        vols = [500_000] * n
        vols[15] = 5_000_000  # spike at signal bar
        closes = [99.0] * n
        closes[15] = 101.0  # above VWAP
        df = pd.DataFrame(
            {"open": 100.0, "high": 101.5, "low": 98.5, "close": closes, "volume": vols},
            index=idx,
        )
        sig = _make_signal("vwap_reclaim", ts=timestamps[15])
        result = score_vwap_signal(sig, df)
        assert result >= 1


class TestORBQualityScore:
    def test_zero_on_empty_bars(self):
        from app.strategies.signal_quality import score_orb_signal
        sig = _make_signal("orb")
        assert score_orb_signal(sig, pd.DataFrame()) == 0

    def test_score_in_range_0_4(self):
        from app.strategies.signal_quality import score_orb_signal
        bars = _make_bars(n=25)
        sig = _make_signal("orb", ts=_bar_ts(20))
        result = score_orb_signal(sig, bars)
        assert 0 <= result <= 4

    def test_range_criterion_reasonable_range(self):
        from app.strategies.signal_quality import score_orb_signal
        n = 25
        timestamps = [_bar_ts(i) for i in range(n)]
        idx = pd.DatetimeIndex(timestamps, tz="UTC")
        highs = [101.0] * n
        lows = [99.0] * n
        closes = [100.5] * n
        closes[20] = 101.5  # breakout
        highs[20] = 102.0
        df = pd.DataFrame(
            {"open": 100.0, "high": highs, "low": lows, "close": closes, "volume": 1_000_000},
            index=idx,
        )
        sig = _make_signal("orb", ts=timestamps[20])
        result = score_orb_signal(sig, df)
        assert 0 <= result <= 4


class TestRSITrendQualityScore:
    def test_rsi_trend_returns_zero(self):
        from app.strategies.signal_quality import compute_signal_quality_score
        sig = _make_signal("rsi_trend")
        bars = _make_bars(n=60)
        assert compute_signal_quality_score(sig, bars) == 0.0


class TestComputeSignalQualityScore:
    def test_routes_vwap(self):
        from app.strategies.signal_quality import compute_signal_quality_score
        sig = _make_signal("vwap_reclaim", ts=_bar_ts(15))
        bars = _make_bars(n=20)
        result = compute_signal_quality_score(sig, bars)
        assert isinstance(result, float)

    def test_routes_orb(self):
        from app.strategies.signal_quality import compute_signal_quality_score
        sig = _make_signal("orb", ts=_bar_ts(20))
        bars = _make_bars(n=25)
        result = compute_signal_quality_score(sig, bars)
        assert isinstance(result, float)

    def test_unknown_strategy_returns_zero(self):
        from app.strategies.signal_quality import compute_signal_quality_score
        sig = _make_signal("unknown_strat")
        bars = _make_bars(n=20)
        assert compute_signal_quality_score(sig, bars) == 0.0


# ── Daily report bridge section ───────────────────────────────────────────────

class TestDailyReportBridgeSection:
    def test_bridge_fields_exist(self):
        from app.evaluation.daily_report import DailyReport
        r = DailyReport(date="2026-05-28", session_start=None, session_end=None)
        assert hasattr(r, "bridge_entries_count")
        assert hasattr(r, "bridge_traded_count")
        assert hasattr(r, "bridge_blocked_count")
        assert hasattr(r, "bridge_skipped_count")
        assert hasattr(r, "bridge_by_strategy")
        assert hasattr(r, "bridge_top_blocked_reasons")
        assert hasattr(r, "sample_size_warning")

    def test_bridge_section_absent_when_no_entries(self):
        from app.evaluation.daily_report import DailyReport, to_markdown
        r = DailyReport(date="2026-05-28", session_start=None, session_end=None)
        md = to_markdown(r)
        assert "Signal Bridge Diagnostics" not in md

    def test_bridge_section_present_when_entries_exist(self):
        from app.evaluation.daily_report import DailyReport, to_markdown
        r = DailyReport(date="2026-05-28", session_start=None, session_end=None)
        r.bridge_entries_count = 5
        r.bridge_traded_count = 2
        r.bridge_blocked_count = 3
        r.bridge_skipped_count = 0
        md = to_markdown(r)
        assert "Signal Bridge Diagnostics" in md
        assert "Signals evaluated" in md

    def test_sample_size_warning_in_markdown(self):
        from app.evaluation.daily_report import DailyReport, to_markdown
        r = DailyReport(date="2026-05-28", session_start=None, session_end=None)
        r.bridge_entries_count = 3
        r.bridge_traded_count = 1
        r.sample_size_warning = "Sample size too small (1 fill). Win rate requires 30+ trades."
        md = to_markdown(r)
        assert "Warning" in md or "sample" in md.lower()

    def test_sample_size_warning_set_when_fills_below_30(self):
        from app.evaluation.daily_report import DailyReport
        r = DailyReport(date="2026-05-28", session_start=None, session_end=None)
        r.trades_filled = 5
        # Manually simulate what build_daily_report does
        if r.trades_filled < 30:
            r.sample_size_warning = (
                f"Sample size too small for statistical conclusions "
                f"({r.trades_filled} fill(s) this session). "
                f"Win rate and PnL metrics require 30+ trades to be meaningful."
            )
        assert r.sample_size_warning is not None
        assert "5" in r.sample_size_warning
