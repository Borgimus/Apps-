"""Deterministic, idempotent order construction.

Client order IDs are deterministic functions of (trade_id, intent, key inputs) so that a retry
after a timeout re-submits the SAME client_order_id and cannot create a duplicate position.
Entries use marketable-limit / stop-limit orders with a slippage ceiling — never a naked market
order in a fast small-cap.
"""
from __future__ import annotations

import hashlib

from src.broker.interface import OrderRequest


def make_client_order_id(trade_id: str, intent: str, *parts: object) -> str:
    """Deterministic idempotency key for an order intent.

    Same trade + intent + inputs -> same id, so retries are safe. Different inputs -> different id.
    """
    payload = "|".join([trade_id, intent, *[str(p) for p in parts]])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"{intent}-{trade_id}-{digest}"


def build_entry_order(
    *, trade_id: str, symbol: str, qty: int, breakout_level: float, limit_price: float,
) -> OrderRequest:
    """Stop-limit entry: stop at the breakout level, limit at the slippage ceiling."""
    if qty < 1:
        raise ValueError("entry qty must be >= 1")
    if limit_price < breakout_level:
        raise ValueError("limit price must be >= breakout level")
    coid = make_client_order_id(trade_id, "entry", symbol, breakout_level, limit_price, qty)
    return OrderRequest(
        symbol=symbol, side="buy", qty=qty, order_type="stop_limit",
        limit_price=limit_price, stop_price=breakout_level, client_order_id=coid,
    )


def build_protective_stop(*, trade_id: str, symbol: str, qty: int, stop_price: float) -> OrderRequest:
    if qty < 1:
        raise ValueError("stop qty must be >= 1")
    coid = make_client_order_id(trade_id, "stop", symbol, qty, stop_price)
    return OrderRequest(
        symbol=symbol, side="sell", qty=qty, order_type="stop",
        limit_price=None, stop_price=stop_price, client_order_id=coid,
    )


def build_partial_exit(*, trade_id: str, symbol: str, qty: int, limit_price: float) -> OrderRequest:
    if qty < 1:
        raise ValueError("partial qty must be >= 1")
    coid = make_client_order_id(trade_id, "partial", symbol, qty)
    return OrderRequest(
        symbol=symbol, side="sell", qty=qty, order_type="limit",
        limit_price=limit_price, stop_price=None, client_order_id=coid,
    )


def build_final_exit(*, trade_id: str, symbol: str, qty: int, session_date: str) -> OrderRequest:
    if qty < 1:
        raise ValueError("final exit qty must be >= 1")
    # Include session_date so a re-triggered exit on a new session is a distinct, idempotent order.
    coid = make_client_order_id(trade_id, "final", symbol, qty, session_date)
    return OrderRequest(
        symbol=symbol, side="sell", qty=qty, order_type="limit",
        limit_price=None, stop_price=None, client_order_id=coid, time_in_force="opg",
    )
