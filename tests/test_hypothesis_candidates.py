from datetime import date, datetime, time, timedelta

import httpx
import pytest

from app.hypotheses.r1_sector_rotation import SECTORS, rotation
from app.hypotheses.s1_sector_reversal import ReversalDay
from app.hypotheses.s2_failed_breakdown import FailedBreakdown
from app.hypotheses.s3_relative_strength import RelativeStrength
from app.hypotheses.v1_implied_vs_realized import OpraCollector, compare
from app.research.equity_observer import ET
from app.research.replay.engine import run
from app.research.replay.sources import HistoricalSource

DAY = date(2026, 11, 20)
OPEN = datetime.combine(DAY, time(9, 30), ET)


def bars(prices, *, skip=None):
    return [{"t": (OPEN + timedelta(minutes=5*i)).isoformat(), "open": o,
             "high": max(o, c) + .25, "low": min(o, c) - .25, "close": c, "volume": 100}
            for i, (o, c) in enumerate(prices) if i != skip]


def test_s2_requires_completed_break_and_a_contiguous_reclaim():
    prices = [(100, 100)] * 3 + [(100, 98), (98, 100)]
    rule = FailedBreakdown()
    one = next(HistoricalSource({"SPY": bars(prices)}).sessions())
    hit, = run(one, rule)
    assert hit.decided_at == OPEN + timedelta(minutes=26)
    missing = next(HistoricalSource({"SPY": bars(prices, skip=1)}).sessions())
    assert not run(missing, rule)


def test_s1_emits_stabilizing_relative_dip_and_separate_control():
    stock = [(100, 100)] * 4 + [(98, 98), (98, 98.1)]
    rising = [(100, 100.25)] * 6
    falling = [(100, 99.5)] * 6
    for sector, expected in ((rising, "S1"), (falling, "ordinary_dip")):
        rule = ReversalDay({"NVDA": "SMH"}, {"NVDA": (1, .001)}, {"NVDA": .01})
        session = next(HistoricalSource({"NVDA": bars(stock), "SMH": bars(sector)}).sessions())
        found, = run(session, rule)
        assert found.hypothesis_id == expected
        assert found.features["drawdown"] == pytest.approx(.019)


def test_s3_matched_etf_shares_clock_with_signal():
    session = next(HistoricalSource({"NVDA": bars([(100, 101)]*6),
                                     "SMH": bars([(100, 100.2)]*6)}).sessions())
    stock, etf = run(session, RelativeStrength({"NVDA": "SMH"}))
    assert (stock.symbol, etf.symbol) == ("NVDA", "SMH")
    assert stock.decided_at == etf.decided_at == OPEN + timedelta(minutes=31)
    assert etf.control_of == "S3"


def test_rotation_enters_next_month_and_accounts_for_drifted_weights():
    days = [date(2018 + i // 12, i % 12 + 1, 2) for i in range(27)]
    panel = {symbol: {day: 100 * (1 + j / 100) ** i for i, day in enumerate(days)}
             for j, symbol in enumerate(SECTORS, 1)}
    panel["SPY"] = {day: 100 for day in days}
    results, unresolved = rotation(panel)
    first, second, *_ = results["10"]["months"]
    assert not unresolved
    assert first["date"] == days[13].isoformat()
    assert first["arms"]["R1"]["holdings"] == ["XLY", "XLV", "XLU"]
    assert first["arms"]["R1"]["turnover"] == pytest.approx(1)
    assert 0 < second["arms"]["R1"]["turnover"] < 1
    assert second["arms"]["SPY"]["turnover"] == 0


def test_v1_leaves_unmatured_outcomes_unknown_and_replays_opra_get_only():
    days = [DAY + timedelta(days=i) for i in range(12)]
    volatility = compare({"SPY": {d: 100 + i for i, d in enumerate(days)},
                          "^VIX": dict.fromkeys(days, 20.0)}, "^VIX", 5)
    assert volatility["unmatured_horizons"] == 5
    assert volatility["stats"]["all"]["candidate_days"] == 7
    ts = OPEN + timedelta(minutes=15)
    def respond(req):
        assert req.method == "GET" and req.url.params["feed"] == "opra"
        assert req.url.path == "/v1beta1/options/snapshots/SPY"
        return httpx.Response(200, json={"snapshots": {
            "VALID": {"latestQuote": {"bp": 1, "ap": 1.2, "t": ts.isoformat()},
                      "impliedVolatility": .2, "greeks": dict.fromkeys(("delta","gamma","theta","vega"), .1)},
            "MISSING": {"latestQuote": {"bp": 1, "ap": 1.2, "t": ts.isoformat()}}}})
    probe = OpraCollector("demo", "demo", transport=httpx.MockTransport(respond))
    try:
        result = probe.snapshot(first_expiry=DAY, last_expiry=DAY, observed_clock=lambda: ts)
    finally:
        probe.close()
    assert result["unusable_symbols"] == ["MISSING"]
    assert result["records"][0]["iv"] is None
    assert result["records"][1]["greeks_valid"] is True
