"""Breakout crossing, gap ceiling, chase limits, entry limit price."""
from src.scanner.breakout import (
    BreakoutDecision,
    entry_limit_price,
    evaluate_breakout,
    relative_volume_ok,
)

CFG = {
    "max_gap_above_level_pct": 2.0,
    "max_chase_distance_pct": 1.0,
    "order": {"slippage_ceiling_pct": 0.5},
    "volume": {"require_elevated_breakout_volume": True, "min_relative_volume": 1.5},
}


def test_no_cross():
    e = evaluate_breakout(100.0, 99.5, 99.0, CFG)
    assert e.decision == BreakoutDecision.NO_CROSS


def test_valid_trigger():
    e = evaluate_breakout(100.0, 100.5, 99.0, CFG)  # 0.5% above, within chase
    assert e.decision == BreakoutDecision.TRIGGER


def test_chase_reject():
    e = evaluate_breakout(100.0, 101.5, 99.0, CFG)  # 1.5% > 1% chase
    assert e.decision == BreakoutDecision.CHASE_REJECT


def test_gap_reject():
    # reference (prior close/open) already 3% above level -> gap reject
    e = evaluate_breakout(100.0, 103.5, 103.0, CFG)
    assert e.decision == BreakoutDecision.GAP_REJECT


def test_entry_limit_price():
    assert entry_limit_price(100.0, CFG) == 100.5  # +0.5%


def test_relative_volume_requires_normalized_expectation():
    assert relative_volume_ok(2000, 1000, CFG) is True   # rel 2.0 >= 1.5
    assert relative_volume_ok(1000, 1000, CFG) is False  # rel 1.0 < 1.5
    # No valid time-of-day expectation -> fail closed.
    assert relative_volume_ok(999999, 0, CFG) is False
