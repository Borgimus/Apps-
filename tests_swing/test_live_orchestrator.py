"""Live orchestrator: mode gating (SHADOW vs PAPER_AUTO), fail-closed entries, always-manage."""
from datetime import datetime, timedelta, timezone

from src.broker.interface import Account
from src.config import load_config
from src.data.market_data import Bar, Feed, MarketSnapshot
from src.execution.reconciliation import IncidentKind, ReconResult
from src.live.entry import CandidateSetup
from src.live.manage import LivePosition
from src.live.orchestrator import GateFlags, LiveOrchestrator, LoopDeps, MarketView

CFG = load_config().as_dict()
NOW = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)  # 10:00 ET Friday, regular session


class FakeBroker:
    def __init__(self, bp=200_000.0):
        self.bp = bp
        self.submitted = []

    def get_account(self):
        return Account(equity=100_000.0, cash=100_000.0, buying_power=self.bp,
                       endpoint="https://paper-api.alpaca.markets")

    def submit_order(self, order, retry=None, sleep=None):
        self.submitted.append(order)
        return None


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, n):
        self.sent.append(n.event.value)


def fresh_snapshot(symbol="AAA"):
    ts = NOW - timedelta(seconds=5)
    return MarketSnapshot(symbol=symbol, as_of=NOW, feed=Feed.IEX, adjusted=True,
                          bars=[Bar(ts=ts, open=99.0, high=100.5, low=98.0, close=100.0,
                                    volume=1_000_000)])


def market_for(symbol):
    return MarketView(snapshot=fresh_snapshot(symbol), last_price=100.5)


def build(mode="PAPER_AUTO", positions=None, candidates=None, recon=None, flags=None,
          market_fn=None):
    broker = FakeBroker()
    notifier = FakeNotifier()
    deps = LoopDeps(
        broker=broker,
        reconcile=lambda: recon if recon is not None else ReconResult(),
        get_positions=lambda: positions or [],
        get_candidates=lambda: candidates or [],
        get_market=market_fn or market_for,
        notifier=notifier, config=CFG, mode=mode, now=NOW,
        flags=flags or GateFlags(),
    )
    return LiveOrchestrator(deps), broker, notifier


def a_candidate(symbol="AAA"):
    return CandidateSetup(symbol=symbol, breakout_level=100.0, initial_stop=95.0,
                          reference_price=99.0, setup_version="2026-07-31")


def test_paper_auto_submits_entry_and_stop():
    orch, broker, notifier = build(mode="PAPER_AUTO", candidates=[a_candidate()])
    rpt = orch.tick()
    # Entry + attached protective stop both submitted.
    assert len(broker.submitted) == 2
    sides = sorted(o.side for o in broker.submitted)
    assert sides == ["buy", "sell"]
    assert "ENTRY_SUBMITTED" in notifier.sent
    assert not rpt.proposals


def test_shadow_proposes_but_submits_nothing():
    orch, broker, notifier = build(mode="SHADOW", candidates=[a_candidate()])
    rpt = orch.tick()
    assert broker.submitted == []
    assert any(p["kind"] == "ENTRY" for p in rpt.proposals)


def test_emergency_stop_blocks_entry_but_still_manages():
    pos = LivePosition(symbol="ZZZ", trade_id="ZZZ-v1", open_shares=100, entry_price=100.0,
                       initial_stop=95.0, has_stop=True)

    def market_fn(sym):
        # ZZZ has hit its 5R target (125); AAA candidate would trigger an entry.
        return MarketView(snapshot=fresh_snapshot(sym), last_price=125.0)

    orch, broker, notifier = build(mode="PAPER_AUTO", positions=[pos],
                                   candidates=[a_candidate("AAA")],
                                   flags=GateFlags(emergency_stop=True), market_fn=market_fn)
    rpt = orch.tick()
    # Managing still acted (5R partial + breakeven stop) -> 2 sells submitted; no entry.
    assert len(broker.submitted) == 2 and all(o.side == "sell" for o in broker.submitted)
    assert "emergency_stop_active" in rpt.blockers
    assert not any(o.side == "buy" for o in broker.submitted)


def test_missing_stop_blocks_entries_and_flags_risk():
    pos = LivePosition(symbol="ZZZ", trade_id="ZZZ-v1", open_shares=100, entry_price=100.0,
                       initial_stop=95.0, has_stop=False)
    recon = ReconResult()
    recon.add(IncidentKind.MISSING_STOP, "ZZZ", "no stop")
    orch, broker, notifier = build(mode="PAPER_AUTO", positions=[pos],
                                   candidates=[a_candidate("AAA")], recon=recon)
    rpt = orch.tick()
    assert "ZZZ" in rpt.risk_blocked
    assert "STOP_MISSING" in notifier.sent
    assert not any(o.side == "buy" for o in broker.submitted)   # entries blocked


def test_insufficient_buying_power_blocks_entries():
    orch, broker, notifier = build(mode="PAPER_AUTO", candidates=[a_candidate()])
    orch.d.broker.bp = 0.0
    rpt = orch.tick()
    assert "insufficient_buying_power" in rpt.blockers
    assert broker.submitted == []
