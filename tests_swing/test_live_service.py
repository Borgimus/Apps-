"""Live service glue: market-view assembly, live reconciliation, and the tick runner."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.config import load_config
from src.data.market_data import Bar, Feed, MarketSnapshot
from src.execution.reconciliation import ExpectedPosition, IncidentKind
from src.live.service import build_market_view, reconcile_live, run_tick_loop

CFG = load_config().as_dict()
ET = ZoneInfo("America/New_York")


class FakeData:
    def __init__(self, closes):
        self._closes = closes

    def get_daily_snapshot(self, symbol, now=None):
        ts = datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)
        bars = [Bar(ts=ts, open=c, high=c + 1, low=c - 1, close=c, volume=1000)
                for c in self._closes]
        return MarketSnapshot(symbol=symbol, as_of=ts, feed=Feed.IEX, adjusted=True, bars=bars)

    def get_last_trade_price(self, symbol):
        return self._closes[-1]


def test_market_view_post_session_has_daily_close_and_sma10():
    data = FakeData([100.0] * 9 + [95.0])   # 10 closes
    # 16:30 ET on a trading day -> POST_MARKET (session complete).
    now = datetime(2026, 7, 31, 16, 30, tzinfo=ET)
    mv = build_market_view(data, "AAA", config=CFG, now=now)
    assert mv.session_completed is True
    assert mv.daily_close == 95.0
    assert mv.sma10_at_close is not None
    assert mv.last_price == 95.0


def test_market_view_during_session_has_no_daily_close():
    data = FakeData([100.0] * 10)
    now = datetime(2026, 7, 31, 11, 0, tzinfo=ET)   # regular session
    mv = build_market_view(data, "AAA", config=CFG, now=now)
    assert mv.session_completed is False and mv.daily_close is None


class FakeBrokerClient:
    def __init__(self, positions, orders):
        self._p = positions
        self._o = orders

    def list_positions(self):
        return self._p

    def list_orders(self, status):
        return self._o


def test_reconcile_live_flags_missing_stop():
    bc = FakeBrokerClient(
        positions=[{"symbol": "AAA", "qty": "100", "avg_entry_price": "50"}],
        orders=[],  # no protective stop
    )
    res = reconcile_live(bc, expected_positions=[ExpectedPosition("AAA", 100)],
                         known_client_order_ids=set())
    assert any(i.kind is IncidentKind.MISSING_STOP for i in res.incidents)


def test_reconcile_live_clean_with_stop():
    bc = FakeBrokerClient(
        positions=[{"symbol": "AAA", "qty": "100", "avg_entry_price": "50"}],
        orders=[{"client_order_id": "stop-1", "symbol": "AAA", "side": "sell",
                 "type": "stop", "qty": "100", "status": "accepted"}],
    )
    res = reconcile_live(bc, expected_positions=[ExpectedPosition("AAA", 100)],
                         known_client_order_ids={"stop-1"})
    assert res.ok


def test_run_tick_loop_respects_max_ticks_and_stop():
    class FakeOrch:
        def __init__(self):
            self.n = 0

        def tick(self):
            self.n += 1
            return {"tick": self.n}

    orch = FakeOrch()
    slept = []
    ticks = run_tick_loop(orch, interval_seconds=5, sleep=slept.append,
                          should_continue=lambda: True, max_ticks=3)
    assert ticks == 3 and orch.n == 3
    assert len(slept) == 2   # no sleep after the final tick


def test_run_tick_loop_stops_when_signaled():
    class FakeOrch:
        def tick(self):
            return None

    state = {"go": True}

    def cont():
        # stop after the first tick
        if state["go"]:
            state["go"] = False
            return True
        return False

    ticks = run_tick_loop(FakeOrch(), interval_seconds=1, sleep=lambda _s: None,
                          should_continue=cont)
    assert ticks == 1


def test_load_setups_file(tmp_path):
    import json as _json
    from src.live.service import load_setups_file

    # Missing file -> no candidates (safe no-op).
    assert load_setups_file(tmp_path / "nope.json") == []

    f = tmp_path / "setups.json"
    f.write_text(_json.dumps([
        {"symbol": "aaa", "breakout_level": 100.0, "initial_stop": 95.0,
         "reference_price": 99.0, "setup_version": "2026-07-31"},
    ]))
    setups = load_setups_file(f)
    assert len(setups) == 1 and setups[0].symbol == "AAA" and setups[0].breakout_level == 100.0
