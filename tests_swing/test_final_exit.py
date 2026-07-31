"""Final-exit timing (completed daily close < SMA10) and gap-through-stop accounting."""
from src.strategy.final_exit import (
    evaluate_final_exit,
    overnight_gap_pct,
    stop_would_trigger_overnight,
)

CFG = {"policy": "confirm_close_then_next_open", "reference_ma": 10}


def test_triggers_on_completed_close_below_sma10():
    s = evaluate_final_exit(session_completed=True, daily_close=99.0, sma10_at_close=100.0,
                            cfg_final=CFG)
    assert s.triggered and s.reason == "daily_close_below_sma10"


def test_no_trigger_when_session_incomplete():
    # Intraday penetration is NOT a daily-close exit.
    s = evaluate_final_exit(session_completed=False, daily_close=99.0, sma10_at_close=100.0,
                            cfg_final=CFG)
    assert not s.triggered and s.reason == "session_not_complete"


def test_no_trigger_when_close_above_sma10():
    s = evaluate_final_exit(session_completed=True, daily_close=101.0, sma10_at_close=100.0,
                            cfg_final=CFG)
    assert not s.triggered and s.reason == "close_not_below_sma10"


def test_overnight_gap_pct():
    assert overnight_gap_pct(100.0, 95.0) == -5.0  # gap down
    assert overnight_gap_pct(100.0, 103.0) == 3.0


def test_gap_through_stop():
    assert stop_would_trigger_overnight(stop_price=95.0, next_low=94.0) is True
    assert stop_would_trigger_overnight(stop_price=95.0, next_low=96.0) is False
