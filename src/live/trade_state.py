"""Persisted trade-state store for the live loop.

Lets the orchestrator reconstruct each open position's lifecycle state across restarts:
trade_id, symbol, actual VWAP entry, the ORIGINAL initial stop (the R reference), the current
resting stop, whether the one-time 5R partial has already been taken, the running high since
entry, and the client_order_ids this system originated (for reconciliation).

Backed by an atomic JSON file (stdlib only) so it is dependency-light and testable. Broker truth
still wins for share counts — `reconstruct_positions` overlays the persisted metadata onto the
live broker positions and never invents a position the broker does not report.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.live.manage import LivePosition


@dataclass
class TradeState:
    trade_id: str
    symbol: str
    open_shares: int
    entry_price: float
    initial_stop: float               # R reference — never changes after entry
    stop_price: float                 # current resting protective stop
    has_stop: bool = False
    partial_done: bool = False
    high_since_entry: float = 0.0
    client_order_ids: list[str] = field(default_factory=list)
    state: str = "OPEN_INITIAL_RISK"
    closed: bool = False


class TradeStateStore:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self._trades: dict[str, TradeState] = {}
        self._load()

    # -- persistence ---------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for tid, row in data.items():
            self._trades[tid] = TradeState(**row)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {tid: asdict(t) for tid, t in self._trades.items()}
        # Atomic write: temp file in the same dir + os.replace.
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # -- queries -------------------------------------------------------------
    def get(self, trade_id: str) -> TradeState | None:
        return self._trades.get(trade_id)

    def open_trades(self) -> list[TradeState]:
        return [t for t in self._trades.values() if not t.closed]

    def known_client_order_ids(self) -> set[str]:
        ids: set[str] = set()
        for t in self._trades.values():
            ids.update(t.client_order_ids)
        return ids

    def reconstruct_positions(self, broker_positions) -> list[LivePosition]:
        """Overlay persisted state onto live broker positions (broker wins on qty).

        ``broker_positions`` may be BrokerPosition objects or dicts with symbol/qty. A stored open
        trade with no matching broker position is skipped here (surfaced separately by
        reconciliation as MISSING_POSITION), never fabricated.
        """
        by_symbol: dict[str, int] = {}
        for p in broker_positions:
            sym = getattr(p, "symbol", None) or p["symbol"]
            qty = getattr(p, "qty", None)
            qty = int(qty if qty is not None else p["qty"])
            by_symbol[sym] = qty

        out: list[LivePosition] = []
        for t in self.open_trades():
            if t.symbol not in by_symbol:
                continue
            out.append(LivePosition(
                symbol=t.symbol, trade_id=t.trade_id, open_shares=by_symbol[t.symbol],
                entry_price=t.entry_price, initial_stop=t.initial_stop,
                partial_done=t.partial_done, has_stop=t.has_stop,
                high_since_entry=t.high_since_entry,
            ))
        return out

    def expected_positions(self):
        from src.execution.reconciliation import ExpectedPosition
        return [ExpectedPosition(t.symbol, t.open_shares) for t in self.open_trades()]

    # -- mutations (called by the orchestrator only when it actually acts) ----
    def record_entry(self, *, trade_id: str, symbol: str, shares: int, entry_price: float,
                     initial_stop: float, entry_coid: str, stop_coid: str) -> None:
        self._trades[trade_id] = TradeState(
            trade_id=trade_id, symbol=symbol, open_shares=shares, entry_price=entry_price,
            initial_stop=initial_stop, stop_price=initial_stop, has_stop=True,
            client_order_ids=[entry_coid, stop_coid], state="OPEN_INITIAL_RISK",
        )
        self._save()

    def on_partial(self, trade_id: str, *, sold_shares: int, new_stop_price: float | None,
                   partial_coid: str, stop_coid: str | None) -> None:
        t = self._trades.get(trade_id)
        if t is None or t.partial_done:
            return   # idempotent: never take the 5R partial twice
        t.partial_done = True
        t.open_shares = max(0, t.open_shares - sold_shares)
        t.client_order_ids.append(partial_coid)
        if new_stop_price is not None:
            t.stop_price = new_stop_price
        if stop_coid:
            t.client_order_ids.append(stop_coid)
        t.state = "OPEN_BREAKEVEN"
        self._save()

    def on_final_exit(self, trade_id: str, *, exit_coid: str) -> None:
        t = self._trades.get(trade_id)
        if t is None:
            return
        t.client_order_ids.append(exit_coid)
        t.state = "FINAL_EXIT_PENDING"
        self._save()

    def update_high(self, trade_id: str, price: float) -> None:
        t = self._trades.get(trade_id)
        if t and price > t.high_since_entry:
            t.high_since_entry = price
            self._save()

    def close_trade(self, trade_id: str) -> None:
        t = self._trades.get(trade_id)
        if t:
            t.closed = True
            t.state = "CLOSED"
            self._save()

    def sync_open_shares(self, trade_id: str, broker_qty: int) -> None:
        """Reconcile stored qty to broker truth; a broker qty of 0 closes the trade."""
        t = self._trades.get(trade_id)
        if not t:
            return
        t.open_shares = broker_qty
        if broker_qty == 0:
            t.closed = True
            t.state = "CLOSED"
        self._save()
