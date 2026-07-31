"""Live position management: missing-stop, 5R partial + breakeven, daily-close precedence."""
from src.config import load_config
from src.live.manage import (
    FinalExitIntent,
    LivePosition,
    MissingStopIntent,
    PartialIntent,
    manage_open_position,
)

CFG = load_config().as_dict()


def pos(**over):
    base = dict(symbol="AAA", trade_id="AAA-v1", open_shares=100, entry_price=100.0,
                initial_stop=95.0, partial_done=False, has_stop=True, high_since_entry=0.0)
    base.update(over)
    return LivePosition(**base)


def test_missing_stop_is_risk_blocked_only():
    res = manage_open_position(pos(has_stop=False), last_price=130.0, session_completed=False,
                               daily_close=None, sma10_at_close=None, config=CFG)
    assert len(res.intents) == 1 and isinstance(res.intents[0], MissingStopIntent)


def test_five_r_partial_and_breakeven():
    # R = 5, target = 125. last_price 125 -> partial 25 sh, stop -> breakeven (entry 100).
    res = manage_open_position(pos(), last_price=125.0, session_completed=False,
                               daily_close=None, sma10_at_close=None, config=CFG)
    partials = [i for i in res.intents if isinstance(i, PartialIntent)]
    assert partials and partials[0].sell_shares == 25 and partials[0].new_stop_price == 100.0


def test_no_partial_before_target():
    res = manage_open_position(pos(), last_price=120.0, session_completed=False,
                               daily_close=None, sma10_at_close=None, config=CFG)
    assert res.intents == []


def test_final_exit_precedence_over_partial():
    # Even with price above the 5R target, a confirmed daily close < SMA10 exits everything.
    res = manage_open_position(pos(), last_price=130.0, session_completed=True,
                               daily_close=90.0, sma10_at_close=100.0, config=CFG)
    assert len(res.intents) == 1 and isinstance(res.intents[0], FinalExitIntent)
    assert res.intents[0].sell_shares == 100


def test_no_final_exit_when_session_incomplete():
    res = manage_open_position(pos(), last_price=110.0, session_completed=False,
                               daily_close=90.0, sma10_at_close=100.0, config=CFG)
    assert not any(isinstance(i, FinalExitIntent) for i in res.intents)
