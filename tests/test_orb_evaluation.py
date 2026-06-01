"""
Tests for ORB-focused evaluation session features:
  - ORB slot reservation logic
  - BridgeEntry new fields
  - ORB report section in DailyReport
  - ORB forward performance lookup helper
  - Settings orb_slot_reserve_until field
"""

from __future__ import annotations

import pytest
from dataclasses import asdict
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_SESSION_START = datetime(2026, 6, 2, 9, 30, 0, tzinfo=_ET)


# ── Settings ──────────────────────────────────────────────────────────────────

class TestOrbSlotReserveUntilSetting:
    def test_default_value(self, monkeypatch):
        monkeypatch.delenv("ORB_SLOT_RESERVE_UNTIL", raising=False)
        from app.config.settings import Settings
        s = Settings()
        assert s.orb_slot_reserve_until == "11:30"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("ORB_SLOT_RESERVE_UNTIL", "10:45")
        from app.config.settings import Settings
        s = Settings(orb_slot_reserve_until="10:45")
        assert s.orb_slot_reserve_until == "10:45"


# ── BridgeEntry new fields ────────────────────────────────────────────────────

class TestBridgeEntryOrbFields:
    def test_underlying_price_default_none(self):
        from app.trading.bridge_diagnostics import BridgeEntry
        e = BridgeEntry(
            session_date="2026-06-02",
            timestamp=datetime.now(),
            symbol="SPY",
            strategy_id="orb",
            signal_direction="long",
        )
        assert e.underlying_price_at_signal is None

    def test_orb_slot_reserved_default_false(self):
        from app.trading.bridge_diagnostics import BridgeEntry
        e = BridgeEntry(
            session_date="2026-06-02",
            timestamp=datetime.now(),
            symbol="SPY",
            strategy_id="orb",
            signal_direction="long",
        )
        assert e.orb_slot_reserved is False

    def test_orb_fwd_fields_default_none(self):
        from app.trading.bridge_diagnostics import BridgeEntry
        e = BridgeEntry(
            session_date="2026-06-02",
            timestamp=datetime.now(),
            symbol="SPY",
            strategy_id="orb",
            signal_direction="long",
        )
        assert e.orb_fwd_price_5m is None
        assert e.orb_fwd_price_15m is None
        assert e.orb_fwd_price_30m is None
        assert e.orb_fwd_pct_5m is None
        assert e.orb_fwd_pct_15m is None
        assert e.orb_fwd_pct_30m is None

    def test_can_set_underlying_price(self):
        from app.trading.bridge_diagnostics import BridgeEntry
        e = BridgeEntry(
            session_date="2026-06-02",
            timestamp=datetime.now(),
            symbol="AAPL",
            strategy_id="orb",
            signal_direction="long",
            underlying_price_at_signal=192.50,
            orb_slot_reserved=True,
        )
        assert e.underlying_price_at_signal == 192.50
        assert e.orb_slot_reserved is True


# ── DBSignalBridge columns ────────────────────────────────────────────────────

class TestDBSignalBridgeOrbColumns:
    def test_new_columns_exist(self):
        from app.api.models import DBSignalBridge
        cols = {c.key for c in DBSignalBridge.__table__.columns}
        assert "underlying_price_at_signal" in cols
        assert "orb_slot_reserved" in cols
        assert "orb_fwd_price_5m" in cols
        assert "orb_fwd_price_15m" in cols
        assert "orb_fwd_price_30m" in cols
        assert "orb_fwd_pct_5m" in cols
        assert "orb_fwd_pct_15m" in cols
        assert "orb_fwd_pct_30m" in cols


# ── ORB slot reservation logic ────────────────────────────────────────────────

class TestOrbSlotReservation:
    """
    Verify that the reservation flag computation produces the expected bool.
    Logic: active when now < reserve_until AND non_orb_entries >= max_trades - 1
    """

    def _should_reserve(self, now_str: str, reserve_until: str,
                        non_orb_entries: int, max_trades: int) -> bool:
        """Mimic the session_runner reservation logic."""
        now = datetime.strptime(now_str, "%H:%M").replace(
            year=2026, month=6, day=2, tzinfo=_ET
        )
        h, m = map(int, reserve_until.split(":"))
        reserve_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now < reserve_dt and non_orb_entries >= max_trades - 1:
            return True
        return False

    def test_reserves_when_condition_met(self):
        assert self._should_reserve("10:00", "11:30", 2, 3) is True

    def test_no_reserve_before_condition(self):
        # Only 1 non-ORB entry, threshold is 2 (max_trades-1=2), not yet met
        assert self._should_reserve("10:00", "11:30", 1, 3) is False

    def test_no_reserve_after_cutoff(self):
        # Past 11:30
        assert self._should_reserve("11:45", "11:30", 2, 3) is False

    def test_reserves_at_exactly_threshold(self):
        # non_orb_entries == max_trades - 1
        assert self._should_reserve("10:30", "11:30", 2, 3) is True

    def test_no_reserve_zero_entries(self):
        assert self._should_reserve("09:45", "11:30", 0, 3) is False


# ── DailyReport ORB fields ────────────────────────────────────────────────────

class TestDailyReportOrbFields:
    def test_orb_fields_default(self):
        from app.evaluation.daily_report import DailyReport
        r = DailyReport(date="2026-06-02", session_start=None, session_end=None)
        assert r.orb_signals_total == 0
        assert r.orb_signals_traded == 0
        assert r.orb_signals_blocked == 0
        assert r.orb_signals_skipped == 0
        assert r.orb_quality_distribution == {}
        assert r.orb_slot_reserved_events == 0
        assert r.orb_actual_pnl is None
        assert r.orb_avg_quality is None
        assert r.orb_avg_fwd_pct_5m is None
        assert r.orb_avg_fwd_pct_15m is None
        assert r.orb_avg_fwd_pct_30m is None
        assert r.pnl_by_strategy == {}


class TestOrbSection:
    def _make_report(self, **kwargs):
        from app.evaluation.daily_report import DailyReport
        defaults = dict(
            date="2026-06-02",
            session_start="09:30",
            session_end="12:30",
            orb_signals_total=3,
            orb_signals_traded=1,
            orb_signals_blocked=1,
            orb_signals_skipped=1,
            orb_quality_distribution={"2": 1, "3": 2},
            orb_avg_quality=2.67,
            orb_actual_pnl=-15.50,
            pnl_by_strategy={"orb": -15.50, "vwap_reclaim": -40.50},
            orb_avg_fwd_pct_5m=0.0032,
            orb_avg_fwd_pct_15m=-0.0011,
            orb_avg_fwd_pct_30m=0.0055,
            orb_slot_reserved_events=2,
            orb_top_blocked_reasons=["risk: Max trades per day reached: 3/3"],
        )
        defaults.update(kwargs)
        return DailyReport(**defaults)

    def test_orb_section_present_when_signals_exist(self):
        from app.evaluation.daily_report import _orb_section
        r = self._make_report()
        md = _orb_section(r)
        assert "ORB Evaluation" in md
        assert "3" in md          # total signals
        assert "Traded" in md
        assert "Blocked" in md

    def test_orb_section_empty_when_no_signals(self):
        from app.evaluation.daily_report import _orb_section
        r = self._make_report(orb_signals_total=0)
        assert _orb_section(r) == ""

    def test_orb_section_shows_vs_vwap(self):
        from app.evaluation.daily_report import _orb_section
        r = self._make_report()
        md = _orb_section(r)
        assert "vwap_reclaim" in md
        assert "40.50" in md

    def test_orb_section_shows_fwd_performance(self):
        from app.evaluation.daily_report import _orb_section
        r = self._make_report()
        md = _orb_section(r)
        assert "+5 min" in md
        assert "+15 min" in md
        assert "+30 min" in md
        assert "+0.32%" in md    # 0.0032 formatted

    def test_orb_section_shows_reservation_events(self):
        from app.evaluation.daily_report import _orb_section
        r = self._make_report()
        md = _orb_section(r)
        assert "2" in md   # slot reserved events count

    def test_orb_section_quality_distribution(self):
        from app.evaluation.daily_report import _orb_section
        r = self._make_report()
        md = _orb_section(r)
        assert "2/4" in md
        assert "3/4" in md

    def test_to_markdown_includes_orb_section(self):
        from app.evaluation.daily_report import to_markdown
        r = self._make_report()
        md = to_markdown(r)
        assert "ORB Evaluation" in md


# ── Forward performance helpers ───────────────────────────────────────────────

class TestOrbFwdLookup:
    def test_lookup_bar_close_finds_closest_bar(self):
        from app.evaluation.orb_forward_performance import _lookup_bar_close
        from zoneinfo import ZoneInfo
        _UTC = ZoneInfo("UTC")
        base = datetime(2026, 6, 2, 14, 0, 0, tzinfo=_UTC)
        bars = [
            (base, 100.0),
            (base + timedelta(minutes=5), 101.0),
            (base + timedelta(minutes=10), 102.0),
        ]
        # Target 14:07 → closest before is 14:05 → 101.0
        result = _lookup_bar_close(bars, base + timedelta(minutes=7))
        assert result == 101.0

    def test_lookup_bar_close_returns_none_for_far_target(self):
        from app.evaluation.orb_forward_performance import _lookup_bar_close
        from zoneinfo import ZoneInfo
        _UTC = ZoneInfo("UTC")
        base = datetime(2026, 6, 2, 14, 0, 0, tzinfo=_UTC)
        bars = [(base, 100.0)]
        # Target is 30 minutes after only bar — exceeds 10-min window
        result = _lookup_bar_close(bars, base + timedelta(minutes=30))
        assert result is None

    def test_lookup_bar_close_does_not_use_future_bars(self):
        from app.evaluation.orb_forward_performance import _lookup_bar_close
        from zoneinfo import ZoneInfo
        _UTC = ZoneInfo("UTC")
        base = datetime(2026, 6, 2, 14, 0, 0, tzinfo=_UTC)
        bars = [
            (base + timedelta(minutes=5), 101.0),
            (base + timedelta(minutes=10), 102.0),
        ]
        # Target is 14:02 — all bars are AFTER target → no valid bar
        result = _lookup_bar_close(bars, base + timedelta(minutes=2))
        assert result is None

    def test_to_utc_naive_datetime(self):
        from app.evaluation.orb_forward_performance import _to_utc
        naive = datetime(2026, 6, 2, 10, 30, 0)
        utc = _to_utc(naive)
        assert utc.tzinfo is not None

    def test_to_utc_string_input(self):
        from app.evaluation.orb_forward_performance import _to_utc
        utc = _to_utc("2026-06-02 14:30:00")
        assert utc.tzinfo is not None


# ── compute_orb_forward_performance integration ───────────────────────────────

class TestComputeOrbForwardPerformance:
    @pytest.mark.asyncio
    async def test_returns_zero_when_no_orb_rows(self):
        from app.evaluation.orb_forward_performance import compute_orb_forward_performance
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=lambda: [])))
        )
        result = await compute_orb_forward_performance(mock_db, "2026-06-02")
        assert result == 0

    @pytest.mark.asyncio
    async def test_skips_rows_without_underlying_price(self):
        from app.evaluation.orb_forward_performance import compute_orb_forward_performance
        from unittest.mock import patch as _patch
        row = MagicMock()
        row.symbol = "DIA"
        row.signal_direction = "long"
        row.underlying_price_at_signal = None   # no price → skip
        row.timestamp = datetime(2026, 6, 2, 11, 4, 0, tzinfo=_ET)

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=lambda: [row])))
        )

        with _patch(
            "app.evaluation.orb_forward_performance._fetch_bars",
            new=AsyncMock(return_value=[(datetime(2026, 6, 2, 14, 5, tzinfo=ZoneInfo("UTC")), 510.0)]),
        ):
            result = await compute_orb_forward_performance(mock_db, "2026-06-02")
        assert result == 0
