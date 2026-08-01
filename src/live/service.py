"""Glue that assembles the live loop's providers from the Alpaca clients, plus a tick runner.

Kept dependency-light and injected so it is testable without httpx/network:
  * build_market_view  — turn a data client + calendar into the orchestrator's MarketView
    (fresh snapshot, last price, and — once the session is complete — the daily close and SMA10
    used by the final-exit rule).
  * reconcile_live     — map live broker positions/orders into a ReconResult (broker wins).
  * run_tick_loop      — call orchestrator.tick() on an interval with an injected sleep/stop.
"""
from __future__ import annotations

from typing import Callable

import json
from pathlib import Path

from src.data.calendar import SessionState, session_state
from src.execution.reconciliation import (
    BrokerOrder,
    BrokerPosition,
    ExpectedPosition,
    ReconResult,
    reconcile,
)
from src.indicators import sma
from src.live.entry import CandidateSetup
from src.live.orchestrator import MarketView


def load_setups_file(path: str | Path) -> list[CandidateSetup]:
    """Read candidate setups from a JSON file the scanning step produces.

    Mirrors the TC2000 file-handoff philosophy: the deterministic scanner writes validated
    setups (symbol, breakout_level, initial_stop, reference_price, setup_version) to a file; the
    live loop reads them. A missing file yields no candidates (safe no-op), never an error.
    """
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    out: list[CandidateSetup] = []
    for row in data:
        out.append(CandidateSetup(
            symbol=str(row["symbol"]).upper(),
            breakout_level=float(row["breakout_level"]),
            initial_stop=float(row["initial_stop"]),
            reference_price=float(row["reference_price"]),
            setup_version=str(row["setup_version"]),
        ))
    return out


def build_market_view(data_client, symbol: str, *, config: dict, now,
                      high_since_entry: float | None = None) -> MarketView:
    """Assemble a MarketView for one symbol from the Alpaca data client and the calendar."""
    snapshot = data_client.get_daily_snapshot(symbol, now=now)
    last_price = data_client.get_last_trade_price(symbol)

    # The daily close is only authoritative once the regular session has completed.
    completed = session_state(now) is SessionState.POST_MARKET
    daily_close = sma10 = None
    if completed and snapshot.bars:
        closes = [b.close for b in snapshot.bars]
        daily_close = closes[-1]
        ref_ma = int(config["final_exit"].get("reference_ma", 10))
        if len(closes) >= ref_ma:
            sma10 = sma(closes, ref_ma)

    return MarketView(snapshot=snapshot, last_price=last_price, session_completed=completed,
                      daily_close=daily_close, sma10_at_close=sma10,
                      high_since_entry=high_since_entry)


def reconcile_live(broker_client, *, expected_positions: list[ExpectedPosition],
                   known_client_order_ids: set[str]) -> ReconResult:
    """Reconcile live broker truth (positions + open orders) against expectation. Broker wins."""
    bpos = [
        BrokerPosition(symbol=p["symbol"], qty=int(p["qty"]),
                       avg_entry_price=float(p["avg_entry_price"]))
        for p in broker_client.list_positions()
    ]
    border = [
        BrokerOrder(client_order_id=o.get("client_order_id", ""), symbol=o["symbol"],
                    side=o["side"], order_type=o["type"], qty=int(o["qty"]), status=o["status"])
        for o in broker_client.list_orders("open")
    ]
    return reconcile(broker_positions=bpos, broker_orders=border,
                     expected_positions=expected_positions,
                     known_client_order_ids=known_client_order_ids)


def run_tick_loop(orchestrator, *, interval_seconds: float, sleep: Callable[[float], None],
                  should_continue: Callable[[], bool], on_report: Callable[[object], None] | None = None,
                  max_ticks: int | None = None) -> int:
    """Run orchestrator.tick() on an interval until ``should_continue`` is False or max_ticks hit.

    ``sleep`` and ``should_continue`` are injected so the loop is deterministic in tests and can be
    driven by a real clock + shutdown flag in production. Returns the number of ticks executed.
    """
    ticks = 0
    while should_continue() and (max_ticks is None or ticks < max_ticks):
        report = orchestrator.tick()
        if on_report:
            on_report(report)
        ticks += 1
        if should_continue() and (max_ticks is None or ticks < max_ticks):
            sleep(interval_seconds)
    return ticks
