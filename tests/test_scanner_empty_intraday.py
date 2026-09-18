"""
Regression: an intraday frame with no bars must reduce to "no bars today",
never raise.

On 2026-09-14 (the observation cohort's first Monday) the two-day intraday
lookback covered only the weekend and the pre-open minutes, so the provider
returned zero bars for 21 symbols at 09:30. ``_alpaca_bars_to_df`` of an empty
list yields a frame with a RangeIndex, and ``intra_df.index.date`` raised
``'RangeIndex' object has no attribute 'date'``, turning those symbols into
sentinel metrics for the opening scan cycle.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.scanning.yfinance_scanner import YFinanceScanner

ET = ZoneInfo("America/New_York")
TODAY = date(2026, 9, 14)
NOW = datetime(2026, 9, 14, 9, 30, 14, tzinfo=ET)


def _daily(rows: int = 25) -> pd.DataFrame:
    idx = pd.date_range(end="2026-09-11", periods=rows, freq="B", tz="UTC")
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1_000_000},
        index=idx,
    )


def _intraday_today(bars: int = 3) -> pd.DataFrame:
    start = datetime(2026, 9, 14, 9, 30, tzinfo=ET)
    idx = pd.DatetimeIndex([start + timedelta(minutes=5 * i) for i in range(bars)]).tz_convert("UTC")
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.8, "volume": 50_000},
        index=idx,
    )


@pytest.mark.parametrize(
    "frame",
    [
        pd.DataFrame(),                                   # what the batch fetch caches on no bars
        pd.DataFrame({"close": [1.0], "volume": [1]}),    # non-datetime index, defensive
        None,
    ],
)
def test_intraday_metrics_treat_missing_bars_as_no_bars_today(frame):
    scanner = YFinanceScanner()
    result = scanner._compute_intraday(frame, TODAY, NOW)
    assert result == (None, None, None, "unknown", None, None, False, False)


@pytest.mark.parametrize("frame", [pd.DataFrame(), None])
def test_rvol_with_missing_intraday_bars_reports_zero_volume(frame):
    rvol, volume_today, avg_vol = YFinanceScanner._compute_rvol(_daily(), frame, TODAY)
    assert (rvol, volume_today) == (0.0, 0)
    assert avg_vol == 1_000_000


def test_intraday_metrics_still_computed_when_bars_exist():
    scanner = YFinanceScanner()
    close, ts, vwap, rel, orb_high, orb_low, breakout, breakdown = scanner._compute_intraday(
        _intraday_today(), TODAY, NOW
    )
    assert close == pytest.approx(100.8)
    assert ts.tzinfo is not None and ts.date() == TODAY
    assert vwap is not None and rel in {"above", "below", "at"}
    assert orb_high == pytest.approx(101.0) and orb_low == pytest.approx(99.5)
    assert (breakout, breakdown) == (False, False)

    rvol, volume_today, _ = YFinanceScanner._compute_rvol(_daily(), _intraday_today(), TODAY)
    assert volume_today == 150_000 and rvol > 0


def test_batch_fetch_failure_is_counted():
    scanner = YFinanceScanner()
    assert scanner.batch_fetch_failures == 0

    def _boom(*args, **kwargs):
        raise RuntimeError("504 Gateway Timeout")

    scanner._alpaca_fetch_bars = _boom
    import app.scanning.yfinance_scanner as mod

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    original = mod._make_alpaca_client
    mod._make_alpaca_client = lambda: _Client()
    try:
        daily, intra = scanner._batch_fetch(["SPY", "IWM"], TODAY)
    finally:
        mod._make_alpaca_client = original

    assert scanner.batch_fetch_failures == 1
    assert daily == {"SPY": None, "IWM": None}
    assert all(df.empty for df in intra.values())
