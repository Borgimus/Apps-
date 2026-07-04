import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from ureversal.config import load_config
from ureversal.risk import RiskManager

ET = ZoneInfo("America/New_York")
DAY = dt.date(2025, 6, 2)


@pytest.fixture()
def rm(tmp_path):
    cfg = load_config()
    r = RiskManager(cfg, state_path=tmp_path / "risk.json")
    r.kill_switch = tmp_path / "KILL_SWITCH"
    r.start_day(DAY, 100_000)
    return r


def _now(h=9, m=40):
    return dt.datetime(2025, 6, 2, h, m, tzinfo=ET)


def test_window_enforced(rm):
    assert rm.can_enter(_now(9, 40), 100_000).allowed
    assert not rm.can_enter(_now(9, 32), 100_000).allowed
    assert not rm.can_enter(_now(9, 52), 100_000).allowed
    assert not rm.can_enter(_now(11, 0), 100_000).allowed


def test_max_trades_per_day(rm):
    for _ in range(3):
        rm.record_entry()
        rm.record_exit(10.0, DAY)
    d = rm.can_enter(_now(), 100_000)
    assert not d.allowed and "max trades" in d.reason


def test_daily_loss_limit(rm):
    rm.record_entry()
    rm.record_exit(-1500.0, DAY)  # > 1% of 100k
    d = rm.can_enter(_now(), 100_000)
    assert not d.allowed and "loss limit" in d.reason


def test_kill_switch(rm):
    rm.kill_switch.touch()
    d = rm.can_enter(_now(), 100_000)
    assert not d.allowed and "kill switch" in d.reason
    rm.kill_switch.unlink()
    assert rm.can_enter(_now(), 100_000).allowed


def test_consecutive_loss_circuit_breaker(rm):
    day = DAY
    for i in range(3):
        rm.record_entry()
        rm.record_exit(-10.0, day)
        day += dt.timedelta(days=1)
        rm.start_day(day, 100_000)
    d = rm.can_enter(dt.datetime.combine(day, dt.time(9, 40), tzinfo=ET), 100_000)
    assert not d.allowed and "circuit breaker" in d.reason
    rm.manual_reset()
    assert rm.can_enter(dt.datetime.combine(day, dt.time(9, 40), tzinfo=ET), 100_000).allowed


def test_pdt_guard(rm):
    for _ in range(3):
        rm.record_entry()
        rm.record_exit(5.0, DAY)
        rm.state["open_positions"] = 0
    # small account: blocked; large account: only blocked by max trades/day
    d_small = rm.can_enter(_now(), 10_000)
    assert not d_small.allowed
    rm.state["trades_today"] = 0
    d_small2 = rm.can_enter(_now(), 10_000)
    assert not d_small2.allowed and "PDT" in d_small2.reason
    assert rm.can_enter(_now(), 100_000).allowed


def test_position_sizing(rm):
    # 25% of 100k at $500 → 50 shares
    assert rm.position_size(100_000, 500.0) == 50


def test_state_persists(tmp_path):
    cfg = load_config()
    r1 = RiskManager(cfg, state_path=tmp_path / "risk.json")
    r1.start_day(DAY, 100_000)
    r1.record_entry()
    r2 = RiskManager(cfg, state_path=tmp_path / "risk.json")
    assert r2.state["trades_today"] == 1
