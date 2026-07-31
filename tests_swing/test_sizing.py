"""Position sizing, whole-share rounding, stop-distance bounds and caps."""
import pytest

from src.risk.sizing import SizingReject, compute_size, realized_risk

CFG_RISK = {
    "risk_fraction": 0.01,
    "min_stop_distance_pct": 0.5,
    "max_stop_distance_pct": 15.0,
}


def size(**kw):
    base = dict(
        expected_entry_price=100.0,
        initial_stop_price=95.0,
        risk_equity=100_000.0,
        risk_fraction=0.01,
        buying_power=1_000_000.0,
        max_position_notional=1_000_000.0,
        liquidity_cap_shares=None,
        cfg_risk=CFG_RISK,
    )
    base.update(kw)
    return compute_size(**base)


def test_basic_one_percent_sizing():
    # risk budget = 1000; risk/share = 5 -> 200 shares
    r = size()
    assert r.accepted and r.shares == 200
    assert r.risk_dollars == pytest.approx(1000.0)


def test_whole_share_floor():
    # risk/share = 3 -> 1000/3 = 333.33 -> floor 333
    r = size(initial_stop_price=97.0)
    assert r.shares == 333


def test_reject_entry_not_above_stop():
    r = size(initial_stop_price=100.0)
    assert not r.accepted and r.reject_reason == SizingReject.ENTRY_NOT_ABOVE_STOP


def test_reject_stop_too_tight():
    # stop distance 0.2% < 0.5%
    r = size(initial_stop_price=99.8)
    assert not r.accepted and r.reject_reason == SizingReject.STOP_TOO_TIGHT


def test_reject_stop_too_wide():
    r = size(initial_stop_price=80.0)  # 20% > 15%
    assert not r.accepted and r.reject_reason == SizingReject.STOP_TOO_WIDE


def test_buying_power_cap_binds():
    # buying power only affords 50 shares
    r = size(buying_power=5_000.0)
    assert r.shares == 50 and r.binding_cap == "buying_power"


def test_notional_cap_binds():
    r = size(max_position_notional=3_000.0)  # 30 shares
    assert r.shares == 30 and r.binding_cap == "notional"


def test_liquidity_cap_binds():
    r = size(liquidity_cap_shares=10)
    assert r.shares == 10 and r.binding_cap == "liquidity"


def test_size_below_one_share_rejected():
    # tiny equity -> budget 1 -> 0 shares
    r = size(risk_equity=100.0)
    assert not r.accepted and r.reject_reason == SizingReject.SIZE_BELOW_ONE_SHARE


def test_committed_risk_reduces_budget():
    # already committed 900 of 1000 -> only 100 left -> 20 shares
    r = size(already_committed_risk=900.0)
    assert r.shares == 20


def test_risk_fraction_above_ceiling_rejected():
    with pytest.raises(ValueError):
        size(risk_fraction=0.02)


def test_never_risk_more_than_budget_property():
    # realized risk of the sized position never exceeds the 1% budget
    r = size()
    assert realized_risk(100.0, 95.0, r.shares) <= 0.01 * 100_000 + 1e-9
