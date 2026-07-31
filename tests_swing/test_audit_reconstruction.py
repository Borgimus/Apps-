"""Audit reconstruction guarantee.

Given stored inputs (market snapshot OHLCV) + config version, the deterministic indicators
reproduce EXACTLY the values persisted at decision time. This is the core auditability claim:
a decision can be rebuilt from stored inputs, config, and rule outputs.
"""
from src.indicators import adr_pct, dollar_volume, sma
from src.storage.db import connect, init_db
from src.storage.repository import Repository


def _bars():
    # 205 rising daily bars so all four SMAs and ADR are computable.
    bars = []
    for i in range(205):
        c = 50.0 + i * 0.5
        bars.append({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1_000_000})
    return bars


def _compute(ohlcv):
    closes = [b["close"] for b in ohlcv]
    return {
        "sma10": sma(closes, 10),
        "sma20": sma(closes, 20),
        "sma50": sma(closes, 50),
        "sma200": sma(closes, 200),
        "adr_pct": adr_pct(ohlcv, 20),
        "dollar_volume": dollar_volume(ohlcv, 20),
    }


def test_indicators_reconstruct_from_stored_snapshot():
    conn = connect(":memory:")
    init_db(conn)
    repo = Repository(conn)

    ohlcv = _bars()
    values_at_decision = _compute(ohlcv)

    snap_id = repo.record_snapshot(symbol="AAA", as_of="2026-07-31T20:00:00+00:00", feed="iex",
                                   bar_timestamp="2026-07-31T20:00:00+00:00", adjusted=True,
                                   ohlcv=ohlcv, staleness_seconds=5.0)
    repo.record_indicators(snapshot_id=snap_id, symbol="AAA", as_of="2026-07-31T20:00:00+00:00",
                           timeframe="daily", values=values_at_decision)

    # Later: reload the raw snapshot and RECOMPUTE independently.
    stored_snap = repo.get_snapshot(snap_id)
    recomputed = _compute(stored_snap["ohlcv"])
    stored_indicators = repo.get_indicators(snap_id)

    for key, val in recomputed.items():
        assert stored_indicators[key] == val == values_at_decision[key]
