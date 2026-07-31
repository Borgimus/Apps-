"""Pivot detection and full contraction-setup detection."""
from src.scanner.contraction import (
    detect_contraction,
    find_pivot_highs,
    find_pivot_lows,
)


def b(o, h, l, c, v):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


CFG = {
    "min_length_bars": 3,
    "max_length_bars": 15,
    "require_higher_pivot_lows": 2,
    "require_lower_pivot_highs": 2,
    "pivot_strength": 2,
    "require_range_contraction": True,
    "proximity_to_ma": {"reference_mas": [10, 20], "max_distance_pct": 3.0,
                        "max_distance_atr": 1.0, "atr_period": 20},
    "volume": {"require_below_volume_ema": True, "require_flat_to_declining": True},
    "narrow_candle": {"enabled": True, "max_tr_fraction_of_atr": 0.6, "atr_period": 20},
    "_score_weights": {"narrow_candle": 0.5},
}


def test_find_pivots():
    lows = [5, 3, 4, 2, 6, 7, 1, 8]
    # index 3 (value 2) is lower than 2 bars each side? left [5,3? no need 2 bars]; strength 1
    assert 1 in find_pivot_lows(lows, 1)   # 3 < 5 and 3 < 4
    highs = [1, 5, 2, 3, 9, 4]
    assert 1 in find_pivot_highs(highs, 1)  # 5 > 1 and 5 > 2
    assert 4 in find_pivot_highs(highs, 1)  # 9 > 3 and 9 > 4


def _full_bars():
    bars = []
    for i in range(24):  # uptrend closes 76..99
        c = 76 + i
        bars.append(b(c, c + 1, c - 1, c, 200_000))
    consolidation = [
        b(99.0, 100.5, 99.0, 99.5, 150_000),
        b(99.5, 100.4, 99.0, 99.4, 140_000),
        b(99.4, 100.3, 99.0, 99.3, 130_000),
        b(99.3, 99.9, 99.3, 99.6, 110_000),
        b(99.6, 99.9, 99.4, 99.7, 100_000),
        b(99.7, 99.9, 99.5, 99.6, 90_000),
    ]
    bars.extend(consolidation)
    return bars


def test_valid_contraction_setup():
    full = _full_bars()
    window = full[-6:]
    res = detect_contraction(window, CFG, full)
    assert res.is_setup, res.reasons
    assert res.breakout_level == 100.5
    assert res.components["range_contraction"] is True
    assert res.components["proximity_to_ma"] is True
    assert res.components["volume_below_ema"] is True
    assert res.components["volume_flat_to_declining"] is True
    assert res.score > 0


def test_length_out_of_range_rejected():
    full = _full_bars()
    res = detect_contraction(full[-2:], CFG, full)  # too short
    assert not res.is_setup and any("length" in r for r in res.reasons)


def test_no_contraction_when_range_expands():
    full = _full_bars()
    # Widen the last bar so late range exceeds early range.
    window = full[-6:]
    window[-1] = b(99.7, 105.0, 95.0, 99.6, 90_000)
    res = detect_contraction(window, CFG, full)
    assert not res.is_setup and "range_not_contracting" in res.reasons
