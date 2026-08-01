"""Live entry evaluation: accept path + each deterministic rejection."""
from datetime import datetime, timedelta, timezone

from src.broker.interface import Account
from src.config import load_config
from src.data.market_data import Bar, Feed, MarketSnapshot
from src.live.entry import CandidateSetup, evaluate_entry

CFG = load_config().as_dict()
# 2026-07-31 14:00 UTC == 10:00 ET (Friday) -> regular session.
NOW = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)


def snapshot(fresh=True):
    ts = NOW - (timedelta(seconds=5) if fresh else timedelta(minutes=10))
    bars = [Bar(ts=ts, open=99.0, high=100.5, low=98.0, close=100.0, volume=1_000_000)]
    return MarketSnapshot(symbol="AAA", as_of=NOW, feed=Feed.IEX, adjusted=True, bars=bars)


def setup(**over):
    base = dict(symbol="AAA", breakout_level=100.0, initial_stop=95.0,
                reference_price=99.0, setup_version="2026-07-31")
    base.update(over)
    return CandidateSetup(**base)


def account():
    return Account(equity=100_000.0, cash=100_000.0, buying_power=200_000.0,
                   endpoint="https://paper-api.alpaca.markets")


def _eval(s=None, snap=None, last_price=100.5, open_symbols=None, now=NOW):
    return evaluate_entry(s or setup(), snapshot=snap or snapshot(), last_price=last_price,
                          account=account(), open_positions=[],
                          open_symbols=open_symbols if open_symbols is not None else set(),
                          committed_risk=0.0, config=CFG, now=now)


def test_accepts_valid_breakout_and_builds_orders():
    ed = _eval()
    assert ed.accepted
    assert ed.entry_order.order_type == "stop_limit" and ed.entry_order.side == "buy"
    assert ed.stop_order.side == "sell" and ed.stop_order.stop_price == 95.0
    assert ed.shares >= 1


def test_rejects_duplicate():
    ed = _eval(open_symbols={"AAA"})
    assert not ed.accepted and ed.reject_reason == "duplicate_position_or_pending"


def test_rejects_outside_regular_session():
    ed = _eval(now=datetime(2026, 8, 1, 14, 0, tzinfo=timezone.utc))  # Saturday
    assert not ed.accepted and ed.reject_reason == "not_regular_session"


def test_rejects_stale_data():
    ed = _eval(snap=snapshot(fresh=False))
    assert not ed.accepted and ed.reject_reason.startswith("stale_data")


def test_rejects_chase():
    ed = _eval(last_price=102.0)  # >1% above level
    assert not ed.accepted and ed.reject_reason.startswith("breakout_")


def test_rejects_gap():
    ed = _eval(s=setup(reference_price=103.0), last_price=103.5)  # gapped >2% above level
    assert not ed.accepted and ed.reject_reason.startswith("breakout_gap")
