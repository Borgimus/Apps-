"""
Abstract broker interface.

All broker adapters must implement every method here.  Paper-trading and
live-trading share the same interface so strategies are broker-agnostic.

IMPORTANT: This is the *execution* data source. Never use yfinance data for
placing real or paper orders — always call the broker's own quote endpoints.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"
    BUY_TO_OPEN = "buy_to_open"
    BUY_TO_CLOSE = "buy_to_close"
    SELL_TO_OPEN = "sell_to_open"
    SELL_TO_CLOSE = "sell_to_close"


class OrderType(str, Enum):
    LIMIT = "limit"
    # Market orders for options are intentionally blocked by the risk manager.
    # This enum value exists only to represent incoming order types; the risk
    # manager will reject any OrderRequest with type=MARKET before it reaches
    # the broker.
    MARKET = "market"


class OrderStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"           # Alpaca: received but not yet working
    PENDING_NEW = "pending_new"     # Alpaca: submitted to exchange
    NEW = "new"                     # Alpaca: live at exchange
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    CANCELED = "canceled"           # Alpaca uses this spelling
    REJECTED = "rejected"
    EXPIRED = "expired"
    HELD = "held"                   # Alpaca: held for market open


@dataclass
class AccountInfo:
    account_id: str
    equity: Decimal
    cash: Decimal
    buying_power: Decimal
    day_trade_count: int = 0
    is_paper: bool = True
    currency: str = "USD"


@dataclass
class Position:
    symbol: str
    quantity: int
    avg_cost: Decimal
    current_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    is_option: bool = False
    option_symbol: Optional[str] = None


@dataclass
class OptionContract:
    symbol: str          # underlying
    option_symbol: str   # OCC-style, e.g. SPY230915C00420000
    expiration: date
    strike: Decimal
    option_type: str     # "call" or "put"
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: int
    open_interest: int
    implied_volatility: float
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread_pct(self) -> float:
        if self.mid == 0:
            return float("inf")
        return float((self.ask - self.bid) / self.mid)


@dataclass
class OptionChain:
    symbol: str
    expiration: date
    underlying_price: Decimal
    calls: List[OptionContract] = field(default_factory=list)
    puts: List[OptionContract] = field(default_factory=list)
    fetched_at: Optional[datetime] = None


@dataclass
class Quote:
    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: int
    timestamp: datetime

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2


@dataclass
class OptionQuote:
    option_symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: int
    open_interest: int
    implied_volatility: float
    delta: Optional[float]
    timestamp: datetime

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2


@dataclass
class OrderRequest:
    symbol: str
    option_symbol: str
    side: OrderSide
    quantity: int
    order_type: OrderType
    limit_price: Decimal
    time_in_force: str = "day"
    strategy_id: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class OrderResult:
    order_id: str
    status: OrderStatus
    symbol: str
    option_symbol: str
    side: OrderSide
    quantity: int
    limit_price: Decimal
    filled_price: Optional[Decimal] = None
    filled_quantity: int = 0
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    cancel_reason: Optional[str] = None


class BrokerInterface(abc.ABC):
    """All broker adapters must subclass this."""

    # ── Account ───────────────────────────────────────────────────────────────

    @abc.abstractmethod
    async def get_account(self) -> AccountInfo:
        """Return current account snapshot."""

    # ── Positions ─────────────────────────────────────────────────────────────

    @abc.abstractmethod
    async def get_positions(self) -> List[Position]:
        """Return all open positions."""

    # ── Market data (execution-grade, from broker) ────────────────────────────

    @abc.abstractmethod
    async def get_quote(self, symbol: str) -> Quote:
        """Return NBBO or broker quote for an equity symbol."""

    @abc.abstractmethod
    async def get_option_chain(
        self, symbol: str, expiration: date
    ) -> OptionChain:
        """Return full option chain for a given symbol and expiration date."""

    @abc.abstractmethod
    async def get_option_quote(self, option_symbol: str) -> OptionQuote:
        """Return current quote for a single option contract."""

    # ── Orders ────────────────────────────────────────────────────────────────

    @abc.abstractmethod
    async def place_option_order(self, request: OrderRequest) -> OrderResult:
        """Submit a limit option order.  Market orders must be rejected upstream."""

    @abc.abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order.  Returns True if successfully cancelled."""

    @abc.abstractmethod
    async def get_order_status(self, order_id: str) -> OrderResult:
        """Return current status of an order."""

    # ── Convenience ──────────────────────────────────────────────────────────

    async def get_available_expirations(self, symbol: str) -> List[date]:
        """Return sorted list of available option expirations.
        Default implementation — brokers may override for efficiency."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not implement get_available_expirations"
        )

    async def get_orders(
        self,
        status: Optional[OrderStatus] = None,
        limit: int = 100,
    ) -> List[OrderResult]:
        """Return recent orders, optionally filtered by status."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not implement get_orders"
        )
