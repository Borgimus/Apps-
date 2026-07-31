"""Abstract broker interface. Implementations must be paper-only."""
from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class Account:
    equity: float
    cash: float
    buying_power: float
    endpoint: str


@dataclass
class Position:
    symbol: str
    qty: int
    avg_entry_price: float


@dataclass
class OrderRequest:
    symbol: str
    side: str          # "buy" | "sell"
    qty: int
    order_type: str    # "limit" | "stop" | "stop_limit"
    limit_price: float | None
    stop_price: float | None
    client_order_id: str
    time_in_force: str = "day"


@dataclass
class OrderAck:
    client_order_id: str
    broker_order_id: str
    status: str


class BrokerInterface(abc.ABC):
    @abc.abstractmethod
    def get_account(self) -> Account: ...

    @abc.abstractmethod
    def list_positions(self) -> list[Position]: ...

    @abc.abstractmethod
    def submit_order(self, req: OrderRequest) -> OrderAck: ...

    @abc.abstractmethod
    def cancel_order(self, broker_order_id: str) -> None: ...

    @abc.abstractmethod
    def replace_order(self, broker_order_id: str, *, stop_price: float | None = None,
                      limit_price: float | None = None) -> OrderAck: ...
