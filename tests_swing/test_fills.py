"""Fill and cost models: slippage, unfilled, gap-through, partial, fees."""
from src.backtest.fills import (
    FillOutcome,
    commission,
    regulatory_fees,
    simulate_entry,
    simulate_partial,
    simulate_stop,
    total_costs,
)


def test_entry_fills_with_slippage():
    f = simulate_entry(breakout_level=100.0, limit_price=100.5, bar_open=99.0, bar_high=101.0,
                       slippage_pct=0.5, qty=100)
    assert f.outcome is FillOutcome.FILLED and f.price == 100.5


def test_entry_unfilled_when_high_below_level():
    f = simulate_entry(breakout_level=100.0, limit_price=100.5, bar_open=99.0, bar_high=99.9,
                       slippage_pct=0.5, qty=100)
    assert f.outcome is FillOutcome.UNFILLED


def test_entry_unfilled_when_gaps_above_limit():
    f = simulate_entry(breakout_level=100.0, limit_price=100.5, bar_open=102.0, bar_high=103.0,
                       slippage_pct=0.5, qty=100)
    assert f.outcome is FillOutcome.UNFILLED and "gapped" in f.note


def test_stop_intrabar_hit():
    f = simulate_stop(stop_price=95.0, bar_open=99.0, bar_low=94.0, qty=100)
    assert f.outcome is FillOutcome.FILLED and f.price == 95.0


def test_stop_gap_through_fills_at_open():
    f = simulate_stop(stop_price=95.0, bar_open=90.0, bar_low=88.0, qty=100)
    assert f.outcome is FillOutcome.GAP_THROUGH and f.price == 90.0


def test_stop_not_triggered():
    assert simulate_stop(stop_price=95.0, bar_open=99.0, bar_low=96.0, qty=100) is None


def test_partial_fills_at_target():
    f = simulate_partial(target_price=120.0, bar_high=121.0, qty=25, available=100)
    assert f.outcome is FillOutcome.FILLED and f.qty == 25 and f.price == 120.0


def test_partial_capped_by_available():
    f = simulate_partial(target_price=120.0, bar_high=121.0, qty=200, available=50)
    assert f.qty == 50


def test_fees_apply_to_sells_only():
    assert regulatory_fees("buy", 100, 50.0) == 0.0
    assert regulatory_fees("sell", 100, 50.0) > 0.0
    assert commission(100, per_share=0.0) == 0.0
    assert total_costs("sell", 100, 50.0) == regulatory_fees("sell", 100, 50.0)
