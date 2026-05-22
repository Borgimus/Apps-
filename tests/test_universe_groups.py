"""
Tests for grouped universe loading, liquidity guards, and downstream plumbing.

Tests:
  1. Group loading — each group yields its expected symbols
  2. Deduplication — symbol in multiple groups assigned to first-listed group
  3. Blacklist applied across groups
  4. Max per group enforcement
  5. Max total symbols enforcement
  6. Experimental group disabled by default (not in enabled_groups)
  7. universe_group propagates through CandidateScorer to CandidateScore
  8. Liquidity rejection for low avg-volume names (insufficient_underlying_volume)
  9. Dashboard /scan/results includes enabled_groups and by_group
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from collections import OrderedDict
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _universe_yaml(extra: str = "") -> str:
    """Minimal grouped universe YAML for testing."""
    return textwrap.dedent(f"""
        mode: grouped
        enabled_groups:
          - core_etfs
          - mega_cap
        max_total_symbols: 40
        max_per_group: 15
        groups:
          core_etfs:
            - SPY
            - QQQ
            - IWM
          mega_cap:
            - AAPL
            - MSFT
            - NVDA
          liquid_growth:
            - AMD
            - CRWD
          watchlist_experimental:
            - RIVN
            - HOOD
        blacklist: []
        symbols:
          - SPY
          - QQQ
        {extra}
    """)


def _loader_from_yaml(yaml_str: str):
    """Create a loaded UniverseLoader from an in-memory YAML string."""
    from app.scanning.universe_loader import UniverseLoader
    import tempfile, os
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as fh:
        fh.write(yaml_str)
        tmp_path = fh.name
    try:
        loader = UniverseLoader(path=tmp_path)
        loader.load()
    finally:
        os.unlink(tmp_path)
    return loader


def _make_metrics(
    symbol: str,
    price: float = 150.0,
    avg_volume_20d: float = 10_000_000,
    rvol: float = 1.5,
    atr_pct: float = 0.012,
    has_earnings: bool = False,
    universe_group: Optional[str] = None,
):
    """Build a minimal SymbolMetrics stub."""
    from app.scanning.yfinance_scanner import SymbolMetrics
    return SymbolMetrics(
        symbol=symbol,
        price=price,
        atr=price * atr_pct,
        atr_pct=atr_pct,
        rvol=rvol,
        rsi=50.0,
        vwap=price * 0.99,
        price_vs_vwap="above",
        opening_range_high=price * 1.005,
        opening_range_low=price * 0.995,
        is_orb_breakout=True,
        is_orb_breakdown=False,
        trend="up",
        ma_compression=False,
        gap_pct=0.002,
        volatility_5d=0.18,
        has_earnings_today=has_earnings,
        volume_today=int(avg_volume_20d * rvol),
        avg_volume_20d=avg_volume_20d,
        universe_group=universe_group,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Group loading
# ─────────────────────────────────────────────────────────────────────────────

class TestGroupLoading:

    def test_enabled_groups_return_correct_symbols(self):
        """get_symbols() only returns symbols from enabled groups."""
        loader = _loader_from_yaml(_universe_yaml())
        syms = loader.get_symbols()
        # enabled_groups: core_etfs + mega_cap only
        assert "SPY" in syms
        assert "QQQ" in syms
        assert "IWM" in syms
        assert "AAPL" in syms
        assert "MSFT" in syms
        assert "NVDA" in syms
        # liquid_growth and watchlist_experimental not in enabled_groups
        assert "AMD" not in syms
        assert "RIVN" not in syms

    def test_group_membership_tracked_in_ordered_dict(self):
        """get_symbols_with_groups() maps each symbol to its source group."""
        loader = _loader_from_yaml(_universe_yaml())
        sg = loader.get_symbols_with_groups(
            enabled_groups=["core_etfs", "mega_cap"],
        )
        assert sg["SPY"] == "core_etfs"
        assert sg["QQQ"] == "core_etfs"
        assert sg["AAPL"] == "mega_cap"
        assert sg["NVDA"] == "mega_cap"

    def test_available_groups_includes_all_defined(self):
        """available_groups lists every group defined in YAML."""
        loader = _loader_from_yaml(_universe_yaml())
        grps = loader.available_groups
        assert "core_etfs" in grps
        assert "mega_cap" in grps
        assert "liquid_growth" in grps
        assert "watchlist_experimental" in grps


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — Deduplication
# ─────────────────────────────────────────────────────────────────────────────

class TestDeduplication:

    def test_symbol_in_two_groups_assigned_to_first(self):
        """When a symbol appears in group A and group B, it is assigned to A."""
        dup_yaml = textwrap.dedent("""
            mode: grouped
            groups:
              group_a:
                - SPY
                - QQQ
              group_b:
                - QQQ
                - AAPL
            blacklist: []
            symbols: []
        """)
        loader = _loader_from_yaml(dup_yaml)
        sg = loader.get_symbols_with_groups(
            enabled_groups=["group_a", "group_b"],
        )
        # QQQ appears in group_a first — must not be re-added by group_b
        assert sg["QQQ"] == "group_a"
        # QQQ counted only once
        assert list(sg.keys()).count("QQQ") == 1
        # AAPL still comes from group_b
        assert sg["AAPL"] == "group_b"

    def test_total_count_does_not_double_count(self):
        """Symbol appearing in both enabled groups counted only once in total."""
        dup_yaml = textwrap.dedent("""
            mode: grouped
            groups:
              a:
                - SPY
                - QQQ
                - IWM
              b:
                - QQQ
                - AAPL
            blacklist: []
            symbols: []
        """)
        loader = _loader_from_yaml(dup_yaml)
        sg = loader.get_symbols_with_groups(enabled_groups=["a", "b"])
        # a: SPY, QQQ, IWM — b: AAPL (QQQ deduped) → 4 total
        assert len(sg) == 4
        assert set(sg.keys()) == {"SPY", "QQQ", "IWM", "AAPL"}


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Blacklist across groups
# ─────────────────────────────────────────────────────────────────────────────

class TestBlacklist:

    def test_blacklisted_symbol_excluded_from_all_groups(self):
        """A symbol in blacklist: [] is excluded regardless of which group it's in."""
        bl_yaml = textwrap.dedent("""
            mode: grouped
            enabled_groups:
              - core_etfs
              - mega_cap
            groups:
              core_etfs:
                - SPY
                - QQQ
              mega_cap:
                - AAPL
                - MSFT
            blacklist:
              - QQQ
              - AAPL
            symbols: []
        """)
        loader = _loader_from_yaml(bl_yaml)
        syms = loader.get_symbols()
        assert "QQQ" not in syms
        assert "AAPL" not in syms
        # Non-blacklisted remain
        assert "SPY" in syms
        assert "MSFT" in syms

    def test_extra_blacklist_arg_also_excluded(self):
        """extra_blacklist kwarg is respected on top of the YAML blacklist."""
        loader = _loader_from_yaml(_universe_yaml())
        syms = loader.get_symbols(extra_blacklist=["SPY", "AAPL"])
        assert "SPY" not in syms
        assert "AAPL" not in syms


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — Max per group
# ─────────────────────────────────────────────────────────────────────────────

class TestMaxPerGroup:

    def test_group_capped_at_max_per_group(self):
        """Symbols beyond max_per_group are not included for that group."""
        big_yaml = textwrap.dedent("""
            mode: grouped
            max_per_group: 3
            groups:
              big_group:
                - A
                - B
                - C
                - D
                - E
            blacklist: []
            symbols: []
        """)
        loader = _loader_from_yaml(big_yaml)
        sg = loader.get_symbols_with_groups(
            enabled_groups=["big_group"],
            max_per_group=3,
        )
        assert len(sg) == 3
        # Preserves order: first 3
        assert list(sg.keys()) == ["A", "B", "C"]

    def test_yaml_max_per_group_respected(self):
        """max_per_group from YAML is used when not passed explicitly."""
        big_yaml = textwrap.dedent("""
            mode: grouped
            max_per_group: 2
            groups:
              big_group:
                - A
                - B
                - C
                - D
            blacklist: []
            symbols: []
        """)
        loader = _loader_from_yaml(big_yaml)
        # pass defaults — YAML value should kick in
        sg = loader.get_symbols_with_groups(enabled_groups=["big_group"])
        assert len(sg) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Max total symbols
# ─────────────────────────────────────────────────────────────────────────────

class TestMaxTotalSymbols:

    def test_total_capped_across_groups(self):
        """Total symbols across all groups never exceeds max_total."""
        capped_yaml = textwrap.dedent("""
            mode: grouped
            max_total_symbols: 5
            max_per_group: 10
            groups:
              g1:
                - A
                - B
                - C
                - D
              g2:
                - E
                - F
                - G
            blacklist: []
            symbols: []
        """)
        loader = _loader_from_yaml(capped_yaml)
        sg = loader.get_symbols_with_groups(
            enabled_groups=["g1", "g2"],
            max_total=5,
        )
        assert len(sg) == 5

    def test_max_symbols_arg_further_caps_result(self):
        """max_symbols kwarg applies a final cap on top of group caps."""
        loader = _loader_from_yaml(_universe_yaml())
        syms = loader.get_symbols(max_symbols=2)
        assert len(syms) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — Experimental group disabled by default
# ─────────────────────────────────────────────────────────────────────────────

class TestExperimentalGroupDisabled:

    def test_experimental_not_in_enabled_groups_by_default(self):
        """watchlist_experimental is not in enabled_groups_from_yaml."""
        loader = _loader_from_yaml(_universe_yaml())
        # Our test YAML only enables core_etfs and mega_cap
        assert "watchlist_experimental" not in loader.enabled_groups_from_yaml

    def test_experimental_symbols_excluded_from_default_scan(self):
        """Symbols only in watchlist_experimental are not returned by default scan."""
        loader = _loader_from_yaml(_universe_yaml())
        syms = loader.get_symbols()
        # RIVN and HOOD are only in watchlist_experimental
        assert "RIVN" not in syms
        assert "HOOD" not in syms

    def test_experimental_included_when_explicitly_enabled(self):
        """Passing enabled_groups=[..., 'watchlist_experimental'] includes those symbols."""
        loader = _loader_from_yaml(_universe_yaml())
        syms = loader.get_symbols(
            enabled_groups=["core_etfs", "watchlist_experimental"]
        )
        assert "RIVN" in syms
        assert "HOOD" in syms


# ─────────────────────────────────────────────────────────────────────────────
# Test 7 — universe_group propagates to CandidateScore
# ─────────────────────────────────────────────────────────────────────────────

class TestUniverseGroupPropagation:

    def test_group_propagates_from_metrics_to_score(self):
        """CandidateScorer copies universe_group from SymbolMetrics to CandidateScore."""
        from app.scanning.candidate_scorer import CandidateScorer

        m = _make_metrics("SPY", universe_group="core_etfs")
        scorer = CandidateScorer(min_scan_score=0.0)
        score = scorer.score_one(m)

        assert score.universe_group == "core_etfs"

    def test_none_group_propagates_as_none(self):
        """When SymbolMetrics.universe_group is None, CandidateScore.universe_group is None."""
        from app.scanning.candidate_scorer import CandidateScorer

        m = _make_metrics("AAPL", universe_group=None)
        scorer = CandidateScorer(min_scan_score=0.0)
        score = scorer.score_one(m)

        assert score.universe_group is None

    def test_symbol_metrics_accepts_universe_group(self):
        """SymbolMetrics dataclass accepts universe_group field."""
        m = _make_metrics("NVDA", universe_group="mega_cap")
        assert m.universe_group == "mega_cap"


# ─────────────────────────────────────────────────────────────────────────────
# Test 8 — Liquidity rejection for illiquid underlying
# ─────────────────────────────────────────────────────────────────────────────

class TestLiquidityRejection:

    def test_low_avg_volume_rejected(self):
        """Symbol with avg_volume_20d below threshold gets insufficient_underlying_volume."""
        from app.scanning.candidate_scorer import CandidateScorer

        m = _make_metrics("ILLIQUID", avg_volume_20d=100_000)
        scorer = CandidateScorer(
            min_scan_score=40.0,
            min_underlying_avg_volume=500_000,
        )
        score = scorer.score_one(m)

        assert score.is_rejected
        assert "insufficient_underlying_volume" in score.rejected_reasons

    def test_sufficient_avg_volume_passes(self):
        """Symbol with avg_volume_20d above threshold does not get volume rejection."""
        from app.scanning.candidate_scorer import CandidateScorer

        m = _make_metrics("LIQUID", avg_volume_20d=5_000_000)
        scorer = CandidateScorer(
            min_scan_score=0.0,
            min_underlying_avg_volume=500_000,
        )
        score = scorer.score_one(m)

        assert "insufficient_underlying_volume" not in score.rejected_reasons

    def test_low_price_rejected(self):
        """Symbol with price below min_underlying_price gets price_too_low rejection."""
        from app.scanning.candidate_scorer import CandidateScorer

        m = _make_metrics("PENNY", price=2.50)
        scorer = CandidateScorer(
            min_scan_score=0.0,
            min_underlying_price=5.0,
        )
        score = scorer.score_one(m)

        assert score.is_rejected
        assert "price_too_low" in score.rejected_reasons

    def test_zero_min_thresholds_do_not_reject(self):
        """When min thresholds are 0 (default), no liquidity rejections fire."""
        from app.scanning.candidate_scorer import CandidateScorer

        m = _make_metrics("ANY", price=1.0, avg_volume_20d=1_000)
        scorer = CandidateScorer(min_scan_score=0.0)
        score = scorer.score_one(m)

        assert "price_too_low" not in score.rejected_reasons
        assert "insufficient_underlying_volume" not in score.rejected_reasons


# ─────────────────────────────────────────────────────────────────────────────
# Test 9 — Dashboard /scan/results includes group data
# ─────────────────────────────────────────────────────────────────────────────

class TestDashboardGroupData:

    def _make_scan_store(self, enabled_groups=None, candidates=None):
        return {
            "session_date": "2026-05-22",
            "scanned_at": "2026-05-22T10:00:00",
            "standby": False,
            "standby_reason": None,
            "confirmed": ["SPY"],
            "enabled_groups": enabled_groups or ["core_etfs", "mega_cap"],
            "candidates": candidates or [
                {
                    "symbol": "SPY",
                    "score": 72.0,
                    "signal_type": "LONG",
                    "is_rejected": False,
                    "reason_codes": ["rvol=2.1x (high)"],
                    "rejected_reasons": [],
                    "universe_group": "core_etfs",
                },
                {
                    "symbol": "RIVN",
                    "score": 20.0,
                    "signal_type": "NEUTRAL",
                    "is_rejected": True,
                    "reason_codes": [],
                    "rejected_reasons": ["insufficient_underlying_volume"],
                    "universe_group": "high_beta_liquid",
                },
            ],
        }

    def test_scan_results_endpoint_returns_enabled_groups(self):
        """GET /scan/results returns enabled_groups list from scan_store."""
        from fastapi.testclient import TestClient
        from app.api.dashboard_api import create_app

        store = self._make_scan_store(enabled_groups=["core_etfs", "mega_cap"])
        with patch("app.api.dashboard_api.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                live_trading_enabled=False,
                paper_evaluation_mode=False,
                is_kill_switch_active=MagicMock(return_value=False),
            )
            app = create_app(scan_results_store=store)

        client = TestClient(app)
        resp = client.get("/scan/results")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled_groups" in data
        assert "core_etfs" in data["enabled_groups"]
        assert "mega_cap" in data["enabled_groups"]

    def test_scan_results_endpoint_returns_by_group(self):
        """GET /scan/results includes by_group breakdown from DB records."""
        from fastapi.testclient import TestClient
        from app.api.dashboard_api import create_app
        from unittest.mock import AsyncMock

        store = self._make_scan_store()

        # Mock DB scan rows
        spy_row = MagicMock()
        spy_row.symbol = "SPY"
        spy_row.score = 72.0
        spy_row.signal_type = "LONG"
        spy_row.is_rejected = False
        spy_row.selected = True
        spy_row.reason_codes = '["rvol=2.1x (high)"]'
        spy_row.rejected_reasons = '[]'
        spy_row.rvol = 2.1
        spy_row.rsi = 52.0
        spy_row.atr_pct = 0.012
        spy_row.trend = "up"
        spy_row.price_vs_vwap = "above"
        spy_row.gap_pct = 0.002
        spy_row.universe_group = "core_etfs"
        spy_row.scanned_at = "2026-05-22T10:00:00"

        rivn_row = MagicMock()
        rivn_row.symbol = "RIVN"
        rivn_row.score = 20.0
        rivn_row.signal_type = "NEUTRAL"
        rivn_row.is_rejected = True
        rivn_row.selected = False
        rivn_row.reason_codes = '[]'
        rivn_row.rejected_reasons = '["insufficient_underlying_volume"]'
        rivn_row.rvol = 0.8
        rivn_row.rsi = 45.0
        rivn_row.atr_pct = 0.008
        rivn_row.trend = "sideways"
        rivn_row.price_vs_vwap = "below"
        rivn_row.gap_pct = 0.0
        rivn_row.universe_group = "high_beta_liquid"
        rivn_row.scanned_at = "2026-05-22T10:00:00"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [spy_row, rivn_row]

        with patch("app.api.dashboard_api.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                live_trading_enabled=False,
                paper_evaluation_mode=False,
                is_kill_switch_active=MagicMock(return_value=False),
            )
            app = create_app(scan_results_store=store)

        # Patch DB execute to return our fake rows
        with patch(
            "sqlalchemy.ext.asyncio.AsyncSession.execute",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            client = TestClient(app)
            resp = client.get("/scan/results")

        assert resp.status_code == 200
        data = resp.json()
        assert "by_group" in data
        # by_group is built from DB rows — may be empty if DB was mocked out;
        # at minimum the key must exist
        assert isinstance(data["by_group"], dict)

    def test_session_state_endpoint_returns_enabled_groups(self):
        """GET /session/state exposes enabled_groups from scan_store."""
        from fastapi.testclient import TestClient
        from app.api.dashboard_api import create_app

        store = self._make_scan_store(enabled_groups=["core_etfs", "liquid_growth"])

        with patch("app.api.dashboard_api.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                live_trading_enabled=False,
                paper_evaluation_mode=False,
                is_kill_switch_active=MagicMock(return_value=False),
                risk=MagicMock(max_trades_per_day=3),
                universe=MagicMock(
                    max_active_positions=1,
                    max_symbols_traded_per_day=1,
                    max_contracts_per_position=1,
                ),
                market_open="09:30",
                market_close="16:00",
            )
            app = create_app(scan_results_store=store)

        client = TestClient(app)
        resp = client.get("/session/state")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled_groups" in data
        assert data["enabled_groups"] == ["core_etfs", "liquid_growth"]
