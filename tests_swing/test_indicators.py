"""Indicator math against hand-computed values; no lookahead."""
import pytest

from src.indicators import (
    adr_pct,
    atr,
    atr_pct,
    dollar_volume,
    ema,
    ema_series,
    lookback_return_pct,
    sma,
    sma_series,
    slope_pct,
    true_range,
)


def b(o, h, l, c, v):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def test_sma_basic():
    assert sma([1, 2, 3, 4, 5], 5) == 3.0
    assert sma([1, 2, 3, 4, 5], 2) == 4.5


def test_sma_series_alignment():
    assert sma_series([1, 2, 3, 4], 2) == [1.5, 2.5, 3.5]


def test_sma_requires_enough_values():
    with pytest.raises(ValueError):
        sma([1, 2], 5)


def test_ema_seed_and_recurrence():
    # k = 2/(3+1) = 0.5, seed 1 -> 1, 1.5, 2.25
    assert ema_series([1, 2, 3], 3) == [1.0, 1.5, 2.25]
    assert ema([1, 2, 3], 3) == 2.25


def test_true_range():
    assert true_range(10, 8, 9) == 2      # high-low dominates
    assert true_range(10, 8, 7) == 3      # high-prev_close dominates
    assert true_range(10, 8, 12) == 4     # low-prev_close dominates (|8-12|)


def test_adr_pct_hand_computed():
    # Two bars with known prev closes.
    bars = [b(10, 11, 9, 10, 100), b(10, 12, 10, 11, 100), b(11, 13, 11, 12, 100)]
    # ADR over 2 bars: bar2 (12-10)/10*100=20 ; bar3 (13-11)/11*100=18.1818...
    val = adr_pct(bars, 2)
    assert round(val, 4) == round((20 + (2 / 11 * 100)) / 2, 4)


def test_adr_pct_needs_prev_close():
    with pytest.raises(ValueError):
        adr_pct([b(10, 11, 9, 10, 100)], 1)


def test_atr_and_pct():
    bars = [b(10, 11, 9, 10, 1), b(10, 12, 10, 11, 1), b(10, 13, 11, 12, 1)]
    # TRs: bar2 max(2, |12-10|, |10-10|)=2 ; bar3 max(2,|13-11|,|11-11|)=2 -> ATR(2)=2
    assert atr(bars, 2) == 2.0
    assert atr_pct(bars, 2) == pytest.approx(2 / 12 * 100)


def test_dollar_volume():
    bars = [b(0, 0, 0, 10, 100), b(0, 0, 0, 20, 200)]  # 10*100=1000 ; 20*200=4000
    assert dollar_volume(bars, 2) == 2500.0


def test_slope_pct():
    # MA series rising from 100 to 110 over lookback 1 -> 10%
    assert slope_pct([100, 110], 1) == pytest.approx(10.0)
    assert slope_pct([100, 90], 1) == pytest.approx(-10.0)


def test_lookback_return_no_future_data():
    closes = [100, 101, 102, 110]
    # return over 3 bars: (110-100)/100*100 = 10
    assert lookback_return_pct(closes, 3) == pytest.approx(10.0)
    # Uses only closes up to the last index; adding a future bar changes only if included.
    with pytest.raises(ValueError):
        lookback_return_pct([100], 3)
