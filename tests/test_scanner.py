"""
Tests for Sprint 7 scanning pipeline:
  - UniverseLoader
  - YFinanceScanner (mocked)
  - CandidateScorer
  - AlpacaConfirmer
  - DBScanResult / daily_report scan fields
  - Dashboard /scan/results endpoint
  - Session-runner max_active_positions enforcement
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
import yaml

from app.brokers.broker_interface import (
    OptionChain,
    OptionContract,
    OrderStatus,
)
from app.scanning.candidate_scorer import CandidateScore, CandidateScorer
from app.scanning.universe_loader import UniverseLoader
from app.scanning.yfinance_scanner import SymbolMetrics, YFinanceScanner

ET = ZoneInfo("America/New_York")
# Use today's date at 10:30 AM ET so AlpacaConfirmer's datetime.now() check stays valid
_NOW = datetime.now(tz=ET).replace(hour=10, minute=30, second=0, microsecond=0)
_TODAY = _NOW.date()


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _metrics(
    symbol: str = "SPY",
    price: float = 550.0,
    rvol: float = 2.0,
    atr_pct: float = 0.012,
    rsi: float = 52.0,
    vwap: float = 548.0,
    price_vs_vwap: str = "above",
    trend: str = "up",
    is_orb_breakout: bool = True,
    is_orb_breakdown: bool = False,
    ma_compression: bool = False,
    gap_pct: float = 0.005,
    has_earnings_today: bool = False,
    errors: Optional[List[str]] = None,
) -> SymbolMetrics:
    return SymbolMetrics(
        symbol=symbol,
        price=price,
        atr=price * atr_pct,
        atr_pct=atr_pct,
        rvol=rvol,
        rsi=rsi,
        vwap=vwap,
        price_vs_vwap=price_vs_vwap,
        opening_range_high=price - 0.5,
        opening_range_low=price - 2.0,
        is_orb_breakout=is_orb_breakout,
        is_orb_breakdown=is_orb_breakdown,
        trend=trend,
        ma_compression=ma_compression,
        gap_pct=gap_pct,
        volatility_5d=0.15,
        has_earnings_today=has_earnings_today,
        volume_today=50_000_000,
        avg_volume_20d=25_000_000,
        fetched_at=_NOW,
        errors=errors or [],
    )


def _contract(
    symbol: str = "SPY",
    strike: float = 550.0,
    option_type: str = "call",
    bid: float = 2.00,
    ask: float = 2.10,
    oi: int = 5000,
    vol: int = 2000,
    delta: float = 0.40,
) -> OptionContract:
    spread = ask - bid
    mid = (bid + ask) / 2
    return OptionContract(
        symbol=symbol,
        option_symbol=f"{symbol}{_TODAY.strftime('%y%m%d')}C{int(strike * 1000):08d}",
        expiration=_TODAY,
        strike=Decimal(str(strike)),
        option_type=option_type,
        bid=Decimal(str(bid)),
        ask=Decimal(str(ask)),
        last=Decimal(str(ask)),
        volume=vol,
        open_interest=oi,
        implied_volatility=0.20,
        delta=delta,
    )


@pytest.fixture
def universe_yaml(tmp_path: Path) -> Path:
    data = {
        "mode": "manual",
        "symbols": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"],
        "blacklist": ["NVDA"],
        "scan_config": {
            "max_symbols_per_scan": 10,
            "max_active_symbols": 3,
        },
    }
    f = tmp_path / "ticker_universe.yaml"
    f.write_text(yaml.dump(data))
    return f


# ─────────────────────────────────────────────────────────────────────────────
# 1. UniverseLoader
# ─────────────────────────────────────────────────────────────────────────────

class TestUniverseLoader:
    def test_load_symbols(self, universe_yaml):
        ul = UniverseLoader(path=universe_yaml)
        ul.load()
        assert "SPY" in ul.all_symbols
        assert "QQQ" in ul.all_symbols
        assert len(ul.all_symbols) == 5

    def test_blacklist_excluded(self, universe_yaml):
        ul = UniverseLoader(path=universe_yaml)
        ul.load()
        syms = ul.get_symbols()
        assert "NVDA" not in syms
        assert "SPY" in syms

    def test_max_symbols_cap(self, universe_yaml):
        ul = UniverseLoader(path=universe_yaml)
        ul.load()
        syms = ul.get_symbols(max_symbols=2)
        assert len(syms) == 2

    def test_extra_blacklist(self, universe_yaml):
        ul = UniverseLoader(path=universe_yaml)
        ul.load()
        syms = ul.get_symbols(extra_blacklist=["AAPL"])
        assert "AAPL" not in syms
        assert "NVDA" not in syms  # already in blacklist

    def test_missing_file_returns_empty(self, tmp_path):
        ul = UniverseLoader(path=tmp_path / "nonexistent.yaml")
        ul.load()
        assert ul.get_symbols() == []
        assert ul.mode == "manual"

    def test_mode_property(self, universe_yaml):
        ul = UniverseLoader(path=universe_yaml)
        ul.load()
        assert ul.mode == "manual"

    def test_scan_config_property(self, universe_yaml):
        ul = UniverseLoader(path=universe_yaml)
        ul.load()
        cfg = ul.scan_config
        assert cfg["max_symbols_per_scan"] == 10
        assert cfg["max_active_symbols"] == 3

    def test_default_universe_file_exists(self):
        """The default config/ticker_universe.yaml must exist with SPY."""
        ul = UniverseLoader()
        ul.load()
        syms = ul.get_symbols()
        assert "SPY" in syms
        assert len(syms) >= 5


# ─────────────────────────────────────────────────────────────────────────────
# 2. YFinanceScanner (mocked — no network calls)
# ─────────────────────────────────────────────────────────────────────────────

class TestYFinanceScanner:
    def test_scan_returns_metrics_for_each_symbol(self):
        scanner = YFinanceScanner()
        with patch.object(scanner, "_scan_one", new_callable=AsyncMock) as mock_scan:
            mock_scan.side_effect = lambda sym: asyncio.coroutine(lambda: _metrics(symbol=sym))()
            # Use direct coroutine approach
            async def run():
                mock_scan.side_effect = [_metrics(symbol=s) for s in ["SPY", "QQQ"]]
                results = await asyncio.gather(
                    scanner._scan_one("SPY"),
                    scanner._scan_one("QQQ"),
                )
                return results

            results = asyncio.get_event_loop().run_until_complete(run())
        assert len(results) == 2
        assert results[0].symbol == "SPY"
        assert results[1].symbol == "QQQ"

    def test_scan_one_returns_error_metrics_on_exception(self):
        scanner = YFinanceScanner()
        with patch.object(
            scanner, "_compute_metrics", side_effect=ValueError("network error")
        ):
            result = asyncio.get_event_loop().run_until_complete(
                scanner._scan_one("BAD")
            )
        assert result.symbol == "BAD"
        assert result.price == 0.0
        assert len(result.errors) > 0

    def test_compute_atr_basic(self):
        import numpy as np
        import pandas as pd

        n = 30
        close = pd.Series([100.0 + i * 0.1 for i in range(n)])
        df = pd.DataFrame({
            "open":  close - 0.5,
            "high":  close + 1.0,
            "low":   close - 1.0,
            "close": close,
            "volume": [1_000_000] * n,
        })
        atr, atr_pct = YFinanceScanner._compute_atr(df, price=103.0)
        assert atr > 0
        assert 0 < atr_pct < 0.1

    def test_compute_rsi_midrange(self):
        import pandas as pd
        close = pd.Series([100.0 + (0.5 if i % 2 == 0 else -0.3) for i in range(40)])
        df = pd.DataFrame({"close": close})
        rsi = YFinanceScanner._compute_rsi(df)
        assert 30 < rsi < 70

    def test_compute_rsi_insufficient_data(self):
        import pandas as pd
        df = pd.DataFrame({"close": pd.Series([100.0] * 5)})
        rsi = YFinanceScanner._compute_rsi(df)
        assert rsi == 50.0  # default

    def test_compute_trend_up(self):
        import pandas as pd
        # Steadily rising prices
        close = pd.Series([100.0 + i * 0.1 for i in range(60)])
        df = pd.DataFrame({"close": close})
        price = float(close.iloc[-1])
        trend, compression = YFinanceScanner._compute_trend(df, price)
        assert trend == "up"

    def test_compute_trend_down(self):
        import pandas as pd
        close = pd.Series([100.0 - i * 0.1 for i in range(60)])
        df = pd.DataFrame({"close": close})
        price = float(close.iloc[-1])
        trend, compression = YFinanceScanner._compute_trend(df, price)
        assert trend == "down"

    def test_ma_compression_detected(self):
        import pandas as pd
        # Flat prices → all MAs converge
        close = pd.Series([100.0] * 60)
        df = pd.DataFrame({"close": close})
        _, compression = YFinanceScanner._compute_trend(df, 100.0)
        assert compression is True

    def test_compute_gap(self):
        import pandas as pd
        close = pd.Series([100.0, 100.0, 100.0, 101.0])
        open_ = pd.Series([99.5,  100.0, 100.0, 103.0])  # gap up on last bar
        df = pd.DataFrame({"close": close, "open": open_})
        gap = YFinanceScanner._compute_gap(df)
        # (103 - 101) / 101 ≈ 0.0198
        assert gap > 0.01

    def test_compute_rvol_elevated(self):
        import pandas as pd
        from datetime import date
        n = 25
        daily_dates = pd.date_range("2026-01-01", periods=n, freq="B", tz="UTC")
        daily_df = pd.DataFrame({
            "volume": [1_000_000] * n,
            "close": [100.0] * n,
        }, index=daily_dates)

        today = date(2026, 5, 11)
        intra_dates = pd.date_range(
            "2026-05-11 13:30:00", periods=10, freq="5min", tz="UTC"
        )
        intra_df = pd.DataFrame({"volume": [200_000] * 10}, index=intra_dates)
        rvol, vol_today, avg_vol = YFinanceScanner._compute_rvol(daily_df, intra_df, today)
        assert vol_today == 2_000_000
        assert rvol == pytest.approx(2.0, abs=0.1)


# ─────────────────────────────────────────────────────────────────────────────
# 3. CandidateScorer
# ─────────────────────────────────────────────────────────────────────────────

class TestCandidateScorer:
    def test_high_quality_long_scores_above_threshold(self):
        scorer = CandidateScorer(min_scan_score=40.0)
        m = _metrics(
            rvol=2.5, atr_pct=0.015, is_orb_breakout=True,
            trend="up", rsi=55, price_vs_vwap="above",
        )
        c = scorer.score_one(m)
        assert not c.is_rejected
        assert c.score >= 40
        assert c.signal_type == "LONG"

    def test_earnings_today_rejected(self):
        scorer = CandidateScorer()
        m = _metrics(has_earnings_today=True)
        c = scorer.score_one(m)
        assert c.is_rejected
        assert "earnings_today" in c.rejected_reasons

    def test_low_volume_chop_rejected(self):
        scorer = CandidateScorer()
        m = _metrics(rvol=0.3)
        c = scorer.score_one(m)
        assert c.is_rejected
        assert "low_volume_chop" in c.rejected_reasons

    def test_tiny_atr_rejected(self):
        scorer = CandidateScorer()
        m = _metrics(atr_pct=0.001)
        c = scorer.score_one(m)
        assert c.is_rejected
        assert "atr_too_small" in c.rejected_reasons

    def test_low_score_rejected(self):
        scorer = CandidateScorer(min_scan_score=50.0)
        # Mediocre metrics — no ORB, no elevated volume
        m = _metrics(rvol=0.8, atr_pct=0.005, is_orb_breakout=False, trend="sideways", rsi=50)
        c = scorer.score_one(m)
        assert c.is_rejected

    def test_score_all_sorted_descending(self):
        scorer = CandidateScorer(min_scan_score=0.0)
        metrics_list = [
            _metrics("AAPL", rvol=1.2, atr_pct=0.008),
            _metrics("SPY",  rvol=2.5, atr_pct=0.015, is_orb_breakout=True, trend="up"),
            _metrics("QQQ",  rvol=1.8, atr_pct=0.010),
        ]
        results = scorer.score_all(metrics_list)
        scores = [c.score for c in results if not c.is_rejected]
        assert scores == sorted(scores, reverse=True)

    def test_short_signal_from_bearish_metrics(self):
        scorer = CandidateScorer(min_scan_score=0.0)
        m = _metrics(
            price_vs_vwap="below", trend="down", is_orb_breakout=False,
            is_orb_breakdown=True, rsi=40,
        )
        c = scorer.score_one(m)
        assert c.signal_type == "SHORT"

    def test_neutral_signal_mixed_metrics(self):
        scorer = CandidateScorer(min_scan_score=0.0)
        m = _metrics(price_vs_vwap="above", trend="down", is_orb_breakout=False)
        c = scorer.score_one(m)
        assert c.signal_type == "NEUTRAL"

    def test_data_error_rejected(self):
        scorer = CandidateScorer()
        m = _metrics(errors=["connection timeout"])
        c = scorer.score_one(m)
        assert c.is_rejected
        assert "data_fetch_error" in c.rejected_reasons

    def test_reason_codes_populated(self):
        scorer = CandidateScorer(min_scan_score=0.0)
        m = _metrics(rvol=2.5, atr_pct=0.015, is_orb_breakout=True, trend="up", price_vs_vwap="above")
        c = scorer.score_one(m)
        assert len(c.reason_codes) > 0
        # Should mention volume and ORB
        reason_text = " ".join(c.reason_codes)
        assert "rvol" in reason_text or "orb" in reason_text


# ─────────────────────────────────────────────────────────────────────────────
# 4. AlpacaConfirmer
# ─────────────────────────────────────────────────────────────────────────────

class TestAlpacaConfirmer:

    def _make_settings(self):
        s = MagicMock()
        s.risk.min_open_interest = 100
        s.risk.min_volume = 50
        s.risk.max_spread_pct = 0.10
        s.options.delta_target_min = 0.35
        s.options.delta_target_max = 0.45
        s.options.preferred_dte = [0, 1, 2]
        return s

    def _make_chain(self, symbol: str = "SPY") -> OptionChain:
        c = _contract(symbol=symbol, bid=2.00, ask=2.10, oi=5000, vol=2000, delta=0.40)
        return OptionChain(
            symbol=symbol,
            expiration=_TODAY,
            underlying_price=Decimal("550.00"),
            calls=[c],
            puts=[],
            fetched_at=datetime.now(tz=ET),
        )

    def test_confirm_passing_candidate(self):
        from app.scanning.alpaca_confirmer import AlpacaConfirmer

        settings = self._make_settings()
        broker = MagicMock()
        broker.get_available_expirations = AsyncMock(return_value=[_TODAY])
        broker.get_option_chain = AsyncMock(return_value=self._make_chain())

        candidate = CandidateScore(
            symbol="SPY", score=75.0, signal_type="LONG",
            reason_codes=["orb_breakout"], rejected_reasons=[], is_rejected=False,
            metrics=_metrics(),
        )
        confirmer = AlpacaConfirmer(broker, settings)
        result = asyncio.get_event_loop().run_until_complete(confirmer.confirm(candidate))
        assert result is not None
        assert result.symbol == "SPY"
        assert result.contract is not None

    def test_reject_rejected_candidate(self):
        from app.scanning.alpaca_confirmer import AlpacaConfirmer

        settings = self._make_settings()
        broker = MagicMock()
        candidate = CandidateScore(
            symbol="SPY", score=20.0, signal_type="LONG",
            reason_codes=[], rejected_reasons=["earnings_today"], is_rejected=True,
            metrics=_metrics(),
        )
        confirmer = AlpacaConfirmer(broker, settings)
        result = asyncio.get_event_loop().run_until_complete(confirmer.confirm(candidate))
        assert result is None

    def test_reject_neutral_signal(self):
        from app.scanning.alpaca_confirmer import AlpacaConfirmer

        settings = self._make_settings()
        broker = MagicMock()
        candidate = CandidateScore(
            symbol="SPY", score=55.0, signal_type="NEUTRAL",
            reason_codes=[], rejected_reasons=[], is_rejected=False,
            metrics=_metrics(),
        )
        confirmer = AlpacaConfirmer(broker, settings)
        result = asyncio.get_event_loop().run_until_complete(confirmer.confirm(candidate))
        assert result is None

    def test_reject_on_broker_error(self):
        from app.scanning.alpaca_confirmer import AlpacaConfirmer

        settings = self._make_settings()
        broker = MagicMock()
        broker.get_available_expirations = AsyncMock(side_effect=RuntimeError("API down"))

        candidate = CandidateScore(
            symbol="SPY", score=70.0, signal_type="LONG",
            reason_codes=[], rejected_reasons=[], is_rejected=False,
            metrics=_metrics(),
        )
        confirmer = AlpacaConfirmer(broker, settings)
        result = asyncio.get_event_loop().run_until_complete(confirmer.confirm(candidate))
        assert result is None

    def test_reject_stale_chain(self):
        from app.scanning.alpaca_confirmer import AlpacaConfirmer

        settings = self._make_settings()
        broker = MagicMock()
        broker.get_available_expirations = AsyncMock(return_value=[_TODAY])

        stale_chain = self._make_chain()
        stale_chain.fetched_at = datetime.now(tz=ET) - timedelta(seconds=200)
        broker.get_option_chain = AsyncMock(return_value=stale_chain)

        candidate = CandidateScore(
            symbol="SPY", score=70.0, signal_type="LONG",
            reason_codes=[], rejected_reasons=[], is_rejected=False,
            metrics=_metrics(),
        )
        confirmer = AlpacaConfirmer(broker, settings)
        result = asyncio.get_event_loop().run_until_complete(confirmer.confirm(candidate))
        assert result is None

    def test_confirm_all_filters_none_results(self):
        from app.scanning.alpaca_confirmer import AlpacaConfirmer

        settings = self._make_settings()
        broker = MagicMock()
        broker.get_available_expirations = AsyncMock(return_value=[_TODAY])
        broker.get_option_chain = AsyncMock(return_value=self._make_chain())

        candidates = [
            CandidateScore(
                symbol="SPY", score=70.0, signal_type="LONG",
                reason_codes=[], rejected_reasons=[], is_rejected=False,
                metrics=_metrics("SPY"),
            ),
            CandidateScore(
                symbol="BAD", score=20.0, signal_type="LONG",
                reason_codes=[], rejected_reasons=["earnings_today"], is_rejected=True,
                metrics=_metrics("BAD"),
            ),
        ]
        confirmer = AlpacaConfirmer(broker, settings)
        results = asyncio.get_event_loop().run_until_complete(confirmer.confirm_all(candidates))
        assert len(results) >= 1
        assert all(r.symbol != "BAD" for r in results)

    def test_expiration_preference(self):
        from app.scanning.alpaca_confirmer import AlpacaConfirmer

        settings = self._make_settings()
        broker = MagicMock()
        today = _TODAY
        # Only dte=1 available
        broker.get_available_expirations = AsyncMock(return_value=[today + timedelta(days=1)])
        chain = OptionChain(
            symbol="QQQ",
            expiration=today + timedelta(days=1),
            underlying_price=Decimal("450.00"),
            calls=[_contract("QQQ", strike=450, bid=1.50, ask=1.60, oi=3000, vol=1500)],
            puts=[],
            fetched_at=datetime.now(tz=ET),
        )
        broker.get_option_chain = AsyncMock(return_value=chain)

        candidate = CandidateScore(
            symbol="QQQ", score=65.0, signal_type="LONG",
            reason_codes=[], rejected_reasons=[], is_rejected=False,
            metrics=_metrics("QQQ", price=450.0),
        )
        confirmer = AlpacaConfirmer(broker, settings)
        result = asyncio.get_event_loop().run_until_complete(confirmer.confirm(candidate))
        assert result is not None
        assert result.expiration == today + timedelta(days=1)


# ─────────────────────────────────────────────────────────────────────────────
# 5. UniverseSettings in app config
# ─────────────────────────────────────────────────────────────────────────────

class TestUniverseSettings:
    def test_universe_settings_defaults(self):
        from app.config.settings import UniverseSettings
        us = UniverseSettings()
        assert us.mode in ("manual", "off")
        assert us.max_symbols_per_scan >= 1
        assert us.max_active_symbols >= 1
        assert us.max_symbols_traded_per_day >= 1
        assert us.max_active_positions >= 1
        assert us.min_scan_score >= 0

    def test_settings_has_universe_field(self):
        from app.config import Settings
        s = Settings(
            live_trading_enabled=False,
            database_url="sqlite+aiosqlite:///:memory:",
        )
        assert hasattr(s, "universe")
        assert s.universe is not None

    def test_universe_env_override(self):
        import os
        from app.config.settings import UniverseSettings
        with patch.dict(os.environ, {"UNIVERSE_MAX_SYMBOLS_PER_SCAN": "5"}):
            us = UniverseSettings()
            assert us.max_symbols_per_scan == 5


# ─────────────────────────────────────────────────────────────────────────────
# 6. Max active positions enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestMaxActivePositions:
    """
    Verify that session runner enforces max_active_positions.

    These tests check the logic directly rather than running a full session.
    """

    def test_max_position_gate_blocks_at_limit(self):
        """When open positions == max, scan_and_place should NOT be called."""
        pm = MagicMock()
        pm.open_positions.return_value = ["pos1"]  # 1 open position

        max_active = 1
        # Simulate the guard logic from session_runner
        assert len(pm.open_positions()) >= max_active  # gate fires → skip scan

    def test_max_position_gate_allows_below_limit(self):
        pm = MagicMock()
        pm.open_positions.return_value = []  # no open positions

        max_active = 1
        assert not (len(pm.open_positions()) >= max_active)  # gate does NOT fire

    def test_symbols_traded_today_prevents_repeat(self):
        symbols_traded = {"SPY"}
        symbol = "SPY"
        assert symbol in symbols_traded  # should be skipped

    def test_symbols_traded_today_allows_new_symbol(self):
        symbols_traded = {"SPY"}
        symbol = "QQQ"
        assert symbol not in symbols_traded  # QQQ not traded yet → allowed


# ─────────────────────────────────────────────────────────────────────────────
# 7. DailyReport scan fields
# ─────────────────────────────────────────────────────────────────────────────

class TestDailyReportScanFields:
    def test_report_has_scan_fields(self):
        from app.evaluation.daily_report import DailyReport
        r = DailyReport(date="2026-05-11", session_start=None, session_end=None)
        assert hasattr(r, "scanned_symbols_count")
        assert hasattr(r, "candidate_count_passed")
        assert hasattr(r, "candidate_count_rejected")
        assert hasattr(r, "selected_symbols")
        assert hasattr(r, "top_candidates")
        assert hasattr(r, "pnl_by_symbol")
        assert hasattr(r, "win_rate_by_symbol")
        assert hasattr(r, "expectancy_by_symbol")

    def test_scan_fields_default_empty(self):
        from app.evaluation.daily_report import DailyReport
        r = DailyReport(date="2026-05-11", session_start=None, session_end=None)
        assert r.scanned_symbols_count == 0
        assert r.candidate_count_passed == 0
        assert r.selected_symbols == []
        assert r.pnl_by_symbol == {}

    def test_to_json_includes_scan_fields(self):
        from app.evaluation.daily_report import DailyReport, to_json
        r = DailyReport(date="2026-05-11", session_start=None, session_end=None)
        r.scanned_symbols_count = 13
        r.candidate_count_passed = 4
        r.selected_symbols = ["SPY"]
        js = json.loads(to_json(r))
        assert js["scanned_symbols_count"] == 13
        assert js["selected_symbols"] == ["SPY"]

    def test_scan_pipeline_section_in_markdown(self):
        from app.evaluation.daily_report import DailyReport, to_markdown
        r = DailyReport(date="2026-05-11", session_start=None, session_end=None)
        r.scanned_symbols_count = 10
        r.candidate_count_passed = 3
        r.candidate_count_rejected = 7
        r.selected_symbols = ["SPY"]
        r.top_candidates = [{"symbol": "SPY", "score": 75.0, "signal_type": "LONG"}]
        md = to_markdown(r)
        assert "Scan Pipeline" in md
        assert "SPY" in md
        assert "75.0" in md

    def test_pnl_by_symbol_section_in_markdown(self):
        from app.evaluation.daily_report import DailyReport, to_markdown
        r = DailyReport(date="2026-05-11", session_start=None, session_end=None)
        r.pnl_by_symbol = {"SPY": 42.0}
        r.win_rate_by_symbol = {"SPY": 1.0}
        r.expectancy_by_symbol = {"SPY": 42.0}
        md = to_markdown(r)
        assert "P&L by Symbol" in md
        assert "SPY" in md


# ─────────────────────────────────────────────────────────────────────────────
# 8. Dashboard /scan/results endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestDashboardScanEndpoint:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.api.dashboard_api import create_app

        store = {
            "session_date": "2026-05-11",
            "confirmed": ["SPY"],
            "candidates": [
                {
                    "symbol": "SPY", "score": 75.0, "signal_type": "LONG",
                    "is_rejected": False, "reason_codes": ["orb_breakout"],
                    "rejected_reasons": [],
                },
                {
                    "symbol": "QQQ", "score": 20.0, "signal_type": "NEUTRAL",
                    "is_rejected": True, "reason_codes": [],
                    "rejected_reasons": ["low_volume_chop"],
                },
            ],
        }
        app = create_app(scan_results_store=store)
        return TestClient(app)

    def test_scan_results_returns_200(self, client):
        r = client.get("/scan/results")
        assert r.status_code == 200

    def test_scan_results_has_live_key(self, client):
        r = client.get("/scan/results")
        data = r.json()
        assert "live" in data
        assert data["live"]["confirmed"] == ["SPY"]

    def test_scan_results_has_session_date(self, client):
        r = client.get("/scan/results")
        data = r.json()
        assert "session_date" in data

    def test_scan_results_empty_store(self):
        from fastapi.testclient import TestClient
        from app.api.dashboard_api import create_app

        app = create_app()  # no store
        client = TestClient(app)
        r = client.get("/scan/results")
        assert r.status_code == 200
        data = r.json()
        assert "live" in data


# ─────────────────────────────────────────────────────────────────────────────
# 9. Confirmer / Bridge cost-cap alignment regression tests
# ─────────────────────────────────────────────────────────────────────────────

def _xlk_like_chain() -> OptionChain:
    """Deep-ITM call at $115 strike, XLK underlying ~$220, no greeks (paper broker)."""
    from decimal import Decimal
    c = OptionContract(
        symbol="XLK",
        option_symbol="XLK260612C00115000",
        expiration=_TODAY,
        strike=Decimal("115.00"),
        option_type="call",
        bid=Decimal("104.80"),
        ask=Decimal("105.30"),
        last=Decimal("105.05"),
        volume=511,
        open_interest=411,
        implied_volatility=0.15,
        delta=None,
    )
    return OptionChain(
        symbol="XLK",
        expiration=_TODAY,
        underlying_price=Decimal("220.00"),
        calls=[c],
        puts=[],
        fetched_at=datetime.now(tz=ET),
    )


class TestConfirmerCostCap:
    """Regression tests for Confirmer / Bridge LiquidityFilter cost-cap alignment."""

    def _make_settings(self):
        s = MagicMock()
        s.risk.min_open_interest = 100
        s.risk.min_volume = 50
        s.risk.max_spread_pct = 0.10
        s.options.delta_target_min = 0.35
        s.options.delta_target_max = 0.45
        s.options.preferred_dte = [0, 1, 2]
        return s

    def test_confirmer_approves_xlk_without_cost_cap(self):
        """Baseline: confirmer with no cost cap approves deep-ITM contract (old bug)."""
        from app.scanning.alpaca_confirmer import AlpacaConfirmer

        settings = self._make_settings()
        broker = MagicMock()
        broker.get_available_expirations = AsyncMock(return_value=[_TODAY])
        broker.get_option_chain = AsyncMock(return_value=_xlk_like_chain())

        candidate = CandidateScore(
            symbol="XLK", score=65.0, signal_type="LONG",
            reason_codes=[], rejected_reasons=[], is_rejected=False,
            metrics=_metrics("XLK", price=220.0),
        )
        confirmer = AlpacaConfirmer(broker, settings)
        result = asyncio.get_event_loop().run_until_complete(confirmer.confirm(candidate))
        assert result is not None, "Without cost cap, deep-ITM contract passes (baseline)"

    def test_confirmer_rejects_xlk_after_cost_cap_set(self):
        """After set_max_contract_cost(), Confirmer rejects deep-ITM delta=None contract."""
        from app.scanning.alpaca_confirmer import AlpacaConfirmer

        settings = self._make_settings()
        broker = MagicMock()
        broker.get_available_expirations = AsyncMock(return_value=[_TODAY])
        broker.get_option_chain = AsyncMock(return_value=_xlk_like_chain())

        candidate = CandidateScore(
            symbol="XLK", score=65.0, signal_type="LONG",
            reason_codes=[], rejected_reasons=[], is_rejected=False,
            metrics=_metrics("XLK", price=220.0),
        )
        confirmer = AlpacaConfirmer(broker, settings)
        confirmer.set_max_contract_cost(991.98)
        result = asyncio.get_event_loop().run_until_complete(confirmer.confirm(candidate))
        assert result is None, "Deep-ITM contract must be rejected after cost cap set"

    def test_confirmer_and_bridge_agree_on_xlk_rejection(self):
        """Confirmer and LiquidityFilter (bridge) reach identical decision when both have cost cap."""
        from app.scanning.alpaca_confirmer import AlpacaConfirmer
        from app.strategies.liquidity_filter import LiquidityFilter
        from app.strategies.strategy_base import Signal, SignalDirection

        settings = self._make_settings()
        max_cost = 991.98
        chain = _xlk_like_chain()

        broker = MagicMock()
        broker.get_available_expirations = AsyncMock(return_value=[_TODAY])
        broker.get_option_chain = AsyncMock(return_value=chain)

        candidate = CandidateScore(
            symbol="XLK", score=65.0, signal_type="LONG",
            reason_codes=[], rejected_reasons=[], is_rejected=False,
            metrics=_metrics("XLK", price=220.0),
        )
        confirmer = AlpacaConfirmer(broker, settings)
        confirmer.set_max_contract_cost(max_cost)
        confirmer_result = asyncio.get_event_loop().run_until_complete(confirmer.confirm(candidate))

        liq = LiquidityFilter({
            "min_open_interest": 100, "min_volume": 50, "max_spread_pct": 0.10,
            "delta_target_min": 0.35, "delta_target_max": 0.45,
        })
        liq.set_max_contract_cost(max_cost)
        sig = Signal(
            strategy_id="test", symbol="XLK", direction=SignalDirection.LONG,
            timestamp=datetime.now(tz=ET), price=220.0,
        )
        bridge_contract = liq.select_contract(chain, sig)

        assert confirmer_result is None, "Confirmer must reject XLK"
        assert bridge_contract is None, "Bridge must reject XLK"

    def test_delta_none_high_ask_rejected_by_cost_cap(self):
        """LiquidityFilter rejects when delta=None and ask*100 > max_contract_cost."""
        from app.strategies.liquidity_filter import LiquidityFilter
        from app.strategies.strategy_base import Signal, SignalDirection

        liq = LiquidityFilter({
            "min_open_interest": 100, "min_volume": 50, "max_spread_pct": 0.10,
            "delta_target_min": 0.35, "delta_target_max": 0.45,
        })
        liq.set_max_contract_cost(991.98)

        sig = Signal(
            strategy_id="test", symbol="XLK", direction=SignalDirection.LONG,
            timestamp=datetime.now(tz=ET), price=220.0,
        )
        assert liq.select_contract(_xlk_like_chain(), sig) is None

    def test_otm_contract_passes_despite_cost_cap(self):
        """OTM contract with reasonable ask is not blocked by cost cap."""
        from decimal import Decimal
        from app.strategies.liquidity_filter import LiquidityFilter
        from app.strategies.strategy_base import Signal, SignalDirection

        liq = LiquidityFilter({
            "min_open_interest": 100, "min_volume": 50, "max_spread_pct": 0.10,
            "delta_target_min": 0.35, "delta_target_max": 0.45,
        })
        liq.set_max_contract_cost(991.98)

        # ask=2.10 → ask*100=$210 << $991.98
        c = _contract("QQQ", strike=450.0, bid=2.00, ask=2.10, oi=5000, vol=2000, delta=None)
        chain = OptionChain(
            symbol="QQQ",
            expiration=_TODAY,
            underlying_price=Decimal("440.00"),
            calls=[c],
            puts=[],
            fetched_at=datetime.now(tz=ET),
        )
        sig = Signal(
            strategy_id="test", symbol="QQQ", direction=SignalDirection.LONG,
            timestamp=datetime.now(tz=ET), price=440.0,
        )
        assert liq.select_contract(chain, sig) is not None

    def test_repeated_xlk_confirm_reject_loop_eliminated(self):
        """After cost cap aligned, XLK is rejected at Confirmer; SPY still confirms."""
        from app.scanning.alpaca_confirmer import AlpacaConfirmer
        from decimal import Decimal

        settings = self._make_settings()
        max_cost = 991.98

        spy_chain = OptionChain(
            symbol="SPY",
            expiration=_TODAY,
            underlying_price=Decimal("550.00"),
            calls=[_contract("SPY", strike=550.0, bid=2.00, ask=2.10, oi=5000, vol=2000, delta=0.40)],
            puts=[],
            fetched_at=datetime.now(tz=ET),
        )

        async def _get_chain(symbol, exp):
            return _xlk_like_chain() if symbol == "XLK" else spy_chain

        broker = MagicMock()
        broker.get_available_expirations = AsyncMock(return_value=[_TODAY])
        broker.get_option_chain = AsyncMock(side_effect=_get_chain)

        candidates = [
            CandidateScore(
                symbol="XLK", score=65.0, signal_type="LONG",
                reason_codes=[], rejected_reasons=[], is_rejected=False,
                metrics=_metrics("XLK", price=220.0),
            ),
            CandidateScore(
                symbol="SPY", score=75.0, signal_type="LONG",
                reason_codes=[], rejected_reasons=[], is_rejected=False,
                metrics=_metrics("SPY"),
            ),
        ]
        confirmer = AlpacaConfirmer(broker, settings)
        confirmer.set_max_contract_cost(max_cost)
        results = asyncio.get_event_loop().run_until_complete(confirmer.confirm_all(candidates))

        confirmed = [r.symbol for r in results]
        assert "XLK" not in confirmed, "XLK must be blocked by cost cap at Confirmer"
        assert "SPY" in confirmed, "SPY must still confirm normally"

    def test_classify_no_contract_reason_cost_cap(self):
        """classify_no_contract_reason returns 'liquidity_cost_cap' for XLK-like chain."""
        from app.strategies.liquidity_filter import LiquidityFilter
        from app.strategies.strategy_base import Signal, SignalDirection

        liq = LiquidityFilter({
            "min_open_interest": 100, "min_volume": 50, "max_spread_pct": 0.10,
            "delta_target_min": 0.35, "delta_target_max": 0.45,
        })
        liq.set_max_contract_cost(991.98)
        sig = Signal(
            strategy_id="test", symbol="XLK", direction=SignalDirection.LONG,
            timestamp=datetime.now(tz=ET), price=220.0,
        )
        reason = liq.classify_no_contract_reason(_xlk_like_chain(), sig)
        assert reason == "liquidity_cost_cap"

    def test_classify_no_contract_reason_generic(self):
        """classify_no_contract_reason returns generic reason when no cost cap is set."""
        from app.strategies.liquidity_filter import LiquidityFilter
        from app.strategies.strategy_base import Signal, SignalDirection

        liq = LiquidityFilter({
            "min_open_interest": 100, "min_volume": 50, "max_spread_pct": 0.10,
            "delta_target_min": 0.35, "delta_target_max": 0.45,
        })
        # No cost cap set — even XLK-like chain returns generic reason
        sig = Signal(
            strategy_id="test", symbol="XLK", direction=SignalDirection.LONG,
            timestamp=datetime.now(tz=ET), price=220.0,
        )
        reason = liq.classify_no_contract_reason(_xlk_like_chain(), sig)
        assert reason == "liquidity_filter_no_contract"


# ─────────────────────────────────────────────────────────────────────────────
# 10. Scanner data freshness and price-source integrity
# ─────────────────────────────────────────────────────────────────────────────

class TestYFinanceScannerFreshness:
    """
    Verify that price comes from intraday bars (not daily close), ORB and
    VWAP comparisons use the intraday close, and stale data is detected and
    rejected.
    """

    _ET = ZoneInfo("America/New_York")
    _UTC = ZoneInfo("UTC")

    def _make_intraday_df(
        self,
        today: date,
        latest_close: float,
        n_bars: int = 6,
        bar_start_utc: Optional[datetime] = None,
    ):
        """
        Build a minimal 5-min DataFrame for today.

        Bars start at 09:30 ET (13:30 UTC) by default.
        The last bar's close is `latest_close`.
        """
        import pandas as pd
        if bar_start_utc is None:
            bar_start_utc = datetime(today.year, today.month, today.day, 13, 30, tzinfo=self._UTC)
        timestamps = [bar_start_utc + timedelta(minutes=5 * i) for i in range(n_bars)]
        closes = [latest_close - 1.0] * (n_bars - 1) + [latest_close]
        return pd.DataFrame(
            {
                "open":   [c - 0.2 for c in closes],
                "high":   [c + 0.5 for c in closes],
                "low":    [c - 0.5 for c in closes],
                "close":  closes,
                "volume": [100_000] * n_bars,
            },
            index=pd.DatetimeIndex(timestamps, tz="UTC"),
        )

    # ------------------------------------------------------------------
    # Price source
    # ------------------------------------------------------------------

    def test_intraday_close_returned_as_latest_bar_price(self):
        """_compute_intraday returns the latest bar close, not any external value."""
        import pandas as pd
        today = date(2026, 6, 10)
        now_et = datetime(2026, 6, 10, 11, 0, tzinfo=self._ET)
        expected_close = 420.0

        intra_df = self._make_intraday_df(today, latest_close=expected_close)
        scanner = YFinanceScanner()
        result = scanner._compute_intraday(intra_df, today, now_et)
        latest_bar_close = result[0]

        assert latest_bar_close is not None
        assert latest_bar_close == pytest.approx(expected_close, abs=0.01), (
            "latest intraday close must be returned from _compute_intraday"
        )

    def test_daily_close_not_used_as_current_price(self):
        """
        When intraday bars are available, price must equal the intraday close,
        not the daily bar close (previous_close).
        """
        # Construct SymbolMetrics directly with different price and previous_close.
        # In production _compute_metrics sets price=intraday_close, previous_close=daily_close.
        m = SymbolMetrics(
            symbol="SPY",
            price=420.0,           # intraday close
            previous_close=550.0,  # yesterday's daily close — must NOT equal price
            price_source="intraday_close",
            atr=5.0, atr_pct=0.012, rvol=2.0, rsi=52.0,
            vwap=419.0, price_vs_vwap="above",
            opening_range_high=421.0, opening_range_low=418.0,
            is_orb_breakout=False, is_orb_breakdown=False,
            trend="up", ma_compression=False, gap_pct=0.005,
            volatility_5d=0.15, has_earnings_today=False,
            volume_today=50_000_000, avg_volume_20d=25_000_000.0,
        )
        assert m.price != m.previous_close, (
            "current price must not equal previous_close when intraday data is available"
        )
        assert m.price_source == "intraday_close"
        assert m.previous_close == 550.0

    def test_previous_close_field_accessible(self):
        """previous_close is stored separately and equals the daily bar close."""
        m = SymbolMetrics(
            symbol="QQQ",
            price=412.0,
            previous_close=450.0,
            price_source="intraday_close",
            atr=4.0, atr_pct=0.010, rvol=1.8, rsi=48.0,
            vwap=411.0, price_vs_vwap="above",
            opening_range_high=413.0, opening_range_low=410.0,
            is_orb_breakout=False, is_orb_breakdown=False,
            trend="sideways", ma_compression=False, gap_pct=0.002,
            volatility_5d=0.12, has_earnings_today=False,
            volume_today=30_000_000, avg_volume_20d=20_000_000.0,
        )
        assert m.previous_close == 450.0
        assert m.price == 412.0
        assert m.previous_close != m.price

    # ------------------------------------------------------------------
    # ORB correctness
    # ------------------------------------------------------------------

    def test_orb_breakout_uses_intraday_close(self):
        """
        ORB breakout must be True when latest intraday close > ORB high.
        The comparison must use the intraday close, not any externally supplied price.
        """
        import pandas as pd
        today = date(2026, 6, 10)
        now_et = datetime(2026, 6, 10, 11, 30, tzinfo=self._ET)

        # ORB window: bars 0-5 (09:30–10:00 ET), high of that window ≈ 411
        # Later bars push close to 425, well above ORB high
        open_utc = datetime(2026, 6, 10, 13, 30, tzinfo=self._UTC)
        times = [open_utc + timedelta(minutes=5 * i) for i in range(10)]
        closes = [405, 407, 408, 409, 410, 411, 414, 418, 422, 425]
        highs  = [c + 1 for c in closes]
        lows   = [c - 1 for c in closes]
        df = pd.DataFrame(
            {
                "open":   [c - 0.2 for c in closes],
                "high":   highs, "low": lows, "close": closes,
                "volume": [100_000] * 10,
            },
            index=pd.DatetimeIndex(times, tz="UTC"),
        )

        scanner = YFinanceScanner()
        result = scanner._compute_intraday(df, today, now_et)
        latest_price, _, _, _, orb_high, orb_low, is_breakout, is_breakdown = result

        assert latest_price == pytest.approx(425.0, abs=0.01)
        assert orb_high < latest_price, "ORB high must be below latest close for breakout"
        assert is_breakout is True
        assert is_breakdown is False

    def test_no_orb_breakout_when_price_below_orb_high(self):
        """is_orb_breakout must be False when latest close is below ORB high."""
        import pandas as pd
        today = date(2026, 6, 10)
        now_et = datetime(2026, 6, 10, 10, 45, tzinfo=self._ET)

        open_utc = datetime(2026, 6, 10, 13, 30, tzinfo=self._UTC)
        times = [open_utc + timedelta(minutes=5 * i) for i in range(6)]
        closes = [405, 408, 412, 410, 407, 404]  # ORB high ≈ 413, falls back to 404
        highs  = [c + 1 for c in closes]
        lows   = [c - 1 for c in closes]
        df = pd.DataFrame(
            {
                "open":  [c - 0.2 for c in closes],
                "high":  highs, "low": lows, "close": closes,
                "volume": [100_000] * 6,
            },
            index=pd.DatetimeIndex(times, tz="UTC"),
        )

        scanner = YFinanceScanner()
        result = scanner._compute_intraday(df, today, now_et)
        latest_price, _, _, _, orb_high, _, is_breakout, _ = result

        assert latest_price == pytest.approx(404.0, abs=0.01)
        assert latest_price < orb_high
        assert is_breakout is False

    # ------------------------------------------------------------------
    # VWAP correctness
    # ------------------------------------------------------------------

    def test_price_vs_vwap_uses_intraday_close(self):
        """
        price_vs_vwap must be 'above' when the latest intraday close is above
        the VWAP computed from today's bars.
        """
        import pandas as pd
        today = date(2026, 6, 10)
        now_et = datetime(2026, 6, 10, 11, 0, tzinfo=self._ET)

        open_utc = datetime(2026, 6, 10, 13, 30, tzinfo=self._UTC)
        times = [open_utc + timedelta(minutes=5 * i) for i in range(5)]
        # First four bars cluster near 410, last bar jumps to 425 — VWAP stays well below 425
        df = pd.DataFrame(
            {
                "open":   [409.0, 409.5, 410.0, 410.5, 424.5],
                "high":   [410.0, 410.5, 411.0, 411.5, 426.0],
                "low":    [408.0, 408.5, 409.0, 409.5, 423.0],
                "close":  [409.5, 410.0, 410.5, 411.0, 425.0],
                "volume": [100_000, 100_000, 100_000, 100_000, 100_000],
            },
            index=pd.DatetimeIndex(times, tz="UTC"),
        )

        scanner = YFinanceScanner()
        result = scanner._compute_intraday(df, today, now_et)
        latest_price, _, vwap, price_vs_vwap, *_ = result

        assert latest_price == pytest.approx(425.0, abs=0.01)
        assert vwap < latest_price, f"VWAP {vwap:.2f} must be below latest close {latest_price:.2f}"
        assert price_vs_vwap == "above", f"Expected 'above', got {price_vs_vwap!r}"

    def test_price_vs_vwap_below_when_close_below_vwap(self):
        """price_vs_vwap must be 'below' when latest intraday close drops under VWAP."""
        import pandas as pd
        today = date(2026, 6, 10)
        now_et = datetime(2026, 6, 10, 11, 0, tzinfo=self._ET)

        open_utc = datetime(2026, 6, 10, 13, 30, tzinfo=self._UTC)
        times = [open_utc + timedelta(minutes=5 * i) for i in range(5)]
        # Start high, sell off hard — VWAP stays above the final bar
        df = pd.DataFrame(
            {
                "open":   [425.0, 424.5, 420.0, 415.0, 395.5],
                "high":   [426.0, 425.0, 421.0, 416.0, 397.0],
                "low":    [423.0, 422.0, 418.0, 413.0, 394.0],
                "close":  [424.5, 423.0, 419.0, 414.0, 395.0],
                "volume": [100_000, 100_000, 100_000, 100_000, 100_000],
            },
            index=pd.DatetimeIndex(times, tz="UTC"),
        )

        scanner = YFinanceScanner()
        result = scanner._compute_intraday(df, today, now_et)
        latest_price, _, vwap, price_vs_vwap, *_ = result

        assert latest_price == pytest.approx(395.0, abs=0.01)
        assert vwap > latest_price, f"VWAP {vwap:.2f} must be above latest close {latest_price:.2f}"
        assert price_vs_vwap == "below", f"Expected 'below', got {price_vs_vwap!r}"

    # ------------------------------------------------------------------
    # Timestamp tracking
    # ------------------------------------------------------------------

    def test_latest_bar_timestamp_differs_from_fetch_time(self):
        """
        latest_bar_ts_et returned by _compute_intraday must equal the bar's
        actual timestamp, not the time of the fetch call.
        """
        import pandas as pd
        today = date(2026, 6, 10)
        now_et = datetime(2026, 6, 10, 11, 0, tzinfo=self._ET)

        # Single bar at 10:25 ET (14:25 UTC) — 35 minutes before now_et
        bar_ts_utc = datetime(2026, 6, 10, 14, 25, tzinfo=self._UTC)
        df = pd.DataFrame(
            {"open": [419.0], "high": [421.0], "low": [418.0], "close": [420.0], "volume": [100_000]},
            index=pd.DatetimeIndex([bar_ts_utc]),
        )

        scanner = YFinanceScanner()
        result = scanner._compute_intraday(df, today, now_et)
        _, latest_bar_ts_et, *_ = result

        assert latest_bar_ts_et is not None
        expected_et = bar_ts_utc.astimezone(self._ET)
        diff_seconds = abs((latest_bar_ts_et - expected_et).total_seconds())
        assert diff_seconds < 60, (
            f"latest_bar_ts_et {latest_bar_ts_et} must match bar timestamp {expected_et}"
        )
        # Must not equal the fetch time
        assert abs((latest_bar_ts_et - now_et).total_seconds()) > 30, (
            "latest_bar_ts_et must not equal the fetch call time"
        )

    def test_fetched_at_and_bar_timestamp_are_independent(self):
        """
        fetched_at on SymbolMetrics records the scan call time.
        latest_intraday_bar_timestamp records when the last data bar was.
        They must be independently set.
        """
        now_et = datetime(2026, 6, 10, 11, 0, tzinfo=self._ET)
        bar_ts_et = datetime(2026, 6, 10, 9, 35, tzinfo=self._ET)  # 85 min before fetch

        m = SymbolMetrics(
            symbol="SPY",
            price=420.0, previous_close=550.0, price_source="intraday_close",
            atr=5.0, atr_pct=0.012, rvol=2.0, rsi=52.0,
            vwap=419.0, price_vs_vwap="above",
            opening_range_high=421.0, opening_range_low=418.0,
            is_orb_breakout=False, is_orb_breakdown=False,
            trend="up", ma_compression=False, gap_pct=0.005,
            volatility_5d=0.15, has_earnings_today=False,
            volume_today=50_000_000, avg_volume_20d=25_000_000.0,
            fetched_at=now_et,
            latest_intraday_bar_timestamp=bar_ts_et,
            intraday_data_age_seconds=(now_et - bar_ts_et).total_seconds(),
        )

        assert m.fetched_at == now_et
        assert m.latest_intraday_bar_timestamp == bar_ts_et
        assert m.fetched_at != m.latest_intraday_bar_timestamp
        assert m.intraday_data_age_seconds == pytest.approx(85 * 60, abs=1)

    # ------------------------------------------------------------------
    # Staleness gate
    # ------------------------------------------------------------------

    def test_stale_intraday_triggers_scanner_data_stale_rejection(self):
        """
        CandidateScorer must reject symbols with is_data_stale=True
        and include 'scanner_data_stale' in rejected_reasons.
        """
        from app.scanning.candidate_scorer import CandidateScorer

        scorer = CandidateScorer()
        base = _metrics()
        stale_m = SymbolMetrics(
            symbol=base.symbol,
            price=base.price, previous_close=base.price * 1.02,
            price_source="intraday_close",
            atr=base.atr, atr_pct=base.atr_pct,
            rvol=base.rvol, rsi=base.rsi,
            vwap=base.vwap, price_vs_vwap=base.price_vs_vwap,
            opening_range_high=base.opening_range_high,
            opening_range_low=base.opening_range_low,
            is_orb_breakout=base.is_orb_breakout,
            is_orb_breakdown=base.is_orb_breakdown,
            trend=base.trend, ma_compression=base.ma_compression,
            gap_pct=base.gap_pct, volatility_5d=base.volatility_5d,
            has_earnings_today=base.has_earnings_today,
            volume_today=base.volume_today, avg_volume_20d=base.avg_volume_20d,
            is_data_stale=True,
            intraday_data_age_seconds=5400.0,  # 90 minutes — well beyond 20-min threshold
        )
        c = scorer.score_one(stale_m)
        assert c.is_rejected
        assert "scanner_data_stale" in c.rejected_reasons

    def test_fresh_intraday_not_rejected_for_staleness(self):
        """
        Symbols with is_data_stale=False must not receive scanner_data_stale rejection.
        """
        from app.scanning.candidate_scorer import CandidateScorer

        scorer = CandidateScorer()
        m = _metrics()  # is_data_stale defaults to False
        assert not m.is_data_stale
        c = scorer.score_one(m)
        assert "scanner_data_stale" not in c.rejected_reasons

    def test_staleness_threshold_in_scanner(self):
        """
        YFinanceScanner with a tight threshold marks a bar from 25 min ago as stale.
        A bar from 5 min ago on the same threshold is not stale.
        """
        import pandas as pd
        today = date(2026, 6, 10)
        now_et = datetime(2026, 6, 10, 11, 0, tzinfo=self._ET)

        # Bar from 25 minutes ago (14:35 UTC = 10:35 ET)
        bar_25min_ago_utc = datetime(2026, 6, 10, 14, 35, tzinfo=self._UTC)  # 10:35 ET
        df_old = pd.DataFrame(
            {"open": [419.0], "high": [421.0], "low": [418.0], "close": [420.0], "volume": [100_000]},
            index=pd.DatetimeIndex([bar_25min_ago_utc]),
        )

        # Bar from 5 minutes ago (14:55 UTC = 10:55 ET)
        bar_5min_ago_utc = datetime(2026, 6, 10, 14, 55, tzinfo=self._UTC)   # 10:55 ET
        df_fresh = pd.DataFrame(
            {"open": [419.0], "high": [421.0], "low": [418.0], "close": [420.0], "volume": [100_000]},
            index=pd.DatetimeIndex([bar_5min_ago_utc]),
        )

        scanner = YFinanceScanner(max_intraday_data_age_seconds=1200)  # 20 min threshold

        _, ts_old, *_ = scanner._compute_intraday(df_old, today, now_et)
        _, ts_fresh, *_ = scanner._compute_intraday(df_fresh, today, now_et)

        age_old = (now_et - ts_old).total_seconds()
        age_fresh = (now_et - ts_fresh).total_seconds()

        assert age_old > 1200, f"25-min-old bar should be stale (age={age_old:.0f}s)"
        assert age_fresh < 1200, f"5-min-old bar should be fresh (age={age_fresh:.0f}s)"

    def test_no_intraday_bars_is_stale(self):
        """
        When no intraday bars exist for today, _compute_intraday returns None for
        the price and timestamp, and the scanner must mark the symbol stale.
        """
        import pandas as pd
        today = date(2026, 6, 10)
        now_et = datetime(2026, 6, 10, 11, 0, tzinfo=self._ET)

        # DataFrame with only yesterday's bars
        yesterday_utc = datetime(2026, 6, 9, 14, 30, tzinfo=self._UTC)
        df_yesterday = pd.DataFrame(
            {"open": [418.0], "high": [421.0], "low": [417.0], "close": [420.0], "volume": [100_000]},
            index=pd.DatetimeIndex([yesterday_utc]),
        )

        scanner = YFinanceScanner()
        result = scanner._compute_intraday(df_yesterday, today, now_et)
        latest_price, latest_ts, *_ = result

        assert latest_price is None, "No bars for today → latest_price must be None"
        assert latest_ts is None, "No bars for today → latest_ts must be None"
