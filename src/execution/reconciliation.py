"""Reconciliation of broker truth against the local (DB) expectation.

Broker REST snapshots and trade-update stream events are the authority for ACTUAL positions and
orders. This module compares broker state to what the system expects and emits incidents. Any
unresolved discrepancy — a position/quantity mismatch, an order the system does not know about,
or a **position without a valid protective stop** — must remain visible and BLOCK new risk
(drives RECON_BLOCKED / RISK_BLOCKED in the state machine).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class IncidentKind(str, Enum):
    POSITION_MISMATCH = "POSITION_MISMATCH"
    UNKNOWN_POSITION = "UNKNOWN_POSITION"     # broker has a position the DB doesn't know
    MISSING_POSITION = "MISSING_POSITION"     # DB expects a position the broker lacks
    ORDER_MISMATCH = "ORDER_MISMATCH"
    UNKNOWN_ORDER = "UNKNOWN_ORDER"
    MISSING_STOP = "MISSING_STOP"


@dataclass
class BrokerPosition:
    symbol: str
    qty: int
    avg_entry_price: float


@dataclass
class BrokerOrder:
    client_order_id: str
    symbol: str
    side: str            # "buy" | "sell"
    order_type: str      # "limit" | "stop" | "stop_limit"
    qty: int
    status: str          # "new"/"accepted"/"filled"/"canceled"/...


@dataclass
class ExpectedPosition:
    symbol: str
    qty: int


@dataclass
class Incident:
    kind: IncidentKind
    symbol: str
    detail: str


@dataclass
class ReconResult:
    incidents: list[Incident] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.incidents

    @property
    def blocks_new_risk(self) -> bool:
        """Any incident blocks new risk until resolved. Broker state wins for actuals."""
        return bool(self.incidents)

    def add(self, kind: IncidentKind, symbol: str, detail: str) -> None:
        self.incidents.append(Incident(kind, symbol, detail))


_OPEN_STATUSES = {"new", "accepted", "pending_new", "partially_filled", "held", "replaced"}


def _covers_stop(orders: list[BrokerOrder], symbol: str, qty: int) -> bool:
    """A long position is protected if open sell stop orders cover its full quantity."""
    covered = sum(
        o.qty for o in orders
        if o.symbol == symbol
        and o.side == "sell"
        and o.order_type in ("stop", "stop_limit")
        and o.status in _OPEN_STATUSES
    )
    return covered >= qty


def reconcile(
    *,
    broker_positions: list[BrokerPosition],
    broker_orders: list[BrokerOrder],
    expected_positions: list[ExpectedPosition],
    known_client_order_ids: set[str],
) -> ReconResult:
    """Compare broker truth to expectation. Broker wins; discrepancies produce incidents.

    A protective-stop check runs for every open long position: a position without stop coverage
    for its full quantity yields a MISSING_STOP incident (→ RISK_BLOCKED).
    """
    res = ReconResult()
    bpos = {p.symbol: p for p in broker_positions}
    epos = {p.symbol: p for p in expected_positions}

    # Position presence / quantity.
    for symbol, bp in bpos.items():
        if symbol not in epos:
            res.add(IncidentKind.UNKNOWN_POSITION, symbol,
                    f"broker has {bp.qty} shares not tracked locally")
        elif epos[symbol].qty != bp.qty:
            res.add(IncidentKind.POSITION_MISMATCH, symbol,
                    f"broker={bp.qty} expected={epos[symbol].qty}")

    for symbol, ep in epos.items():
        if symbol not in bpos:
            res.add(IncidentKind.MISSING_POSITION, symbol,
                    f"expected {ep.qty} shares but broker has none")

    # Protective-stop coverage for every open long position (broker truth).
    for symbol, bp in bpos.items():
        if bp.qty > 0 and not _covers_stop(broker_orders, symbol, bp.qty):
            res.add(IncidentKind.MISSING_STOP, symbol,
                    f"position {bp.qty} lacks a stop covering full qty")

    # Orders the system didn't originate (unknown client order ids).
    for o in broker_orders:
        if o.status in _OPEN_STATUSES and o.client_order_id not in known_client_order_ids:
            res.add(IncidentKind.UNKNOWN_ORDER, o.symbol,
                    f"open order {o.client_order_id!r} not originated by this system")

    return res
