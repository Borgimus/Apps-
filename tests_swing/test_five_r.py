"""5R partial: exactly once, whole-share rounding, breakeven stop move, partial-fill safety."""
from src.strategy.five_r import five_r_target, plan_five_r_partial, r_value

CFG = {
    "r_multiple_trigger": 5.0,
    "fraction": 0.25,
    "move_stop_to_breakeven": True,
    "rounding": "floor",
    "once_only": True,
}


def plan(**kw):
    base = dict(
        high_since_entry=200.0,
        actual_entry_price=100.0,
        initial_stop_price=95.0,
        open_shares=100,
        vwap_entry_price=100.0,
        already_done=False,
        cfg_partial=CFG,
    )
    base.update(kw)
    return plan_five_r_partial(**base)


def test_r_and_target():
    assert r_value(100.0, 95.0) == 5.0
    assert five_r_target(100.0, 95.0) == 125.0  # entry + 5*5


def test_fires_at_target_with_25pct():
    p = plan(high_since_entry=125.0)  # exactly target
    assert p.should_fire and p.sell_shares == 25 and p.remaining_shares == 75
    assert p.new_stop_price == 100.0  # breakeven = vwap entry


def test_does_not_fire_before_target():
    p = plan(high_since_entry=124.99)
    assert not p.should_fire and p.reason == "target_not_reached"


def test_exactly_once():
    p = plan(already_done=True)
    assert not p.should_fire and p.reason == "already_done"


def test_never_sells_zero():
    # 3 shares * 0.25 = 0.75 -> floor 0 -> bumped to 1
    p = plan(open_shares=3)
    assert p.sell_shares == 1 and p.remaining_shares == 2


def test_never_exceeds_open_qty():
    p = plan(open_shares=1, cfg_partial={**CFG, "fraction": 2.0})
    assert p.sell_shares == 1 and p.remaining_shares == 0
    assert p.new_stop_price is None  # nothing remaining -> no breakeven move


def test_breakeven_uses_vwap_not_expected():
    p = plan(vwap_entry_price=101.25)
    assert p.new_stop_price == 101.25
