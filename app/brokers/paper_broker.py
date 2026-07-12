"""
In-process paper broker.

Simulates order fills locally without connecting to any external service.
Used for strategy development and backtesting when no broker credentials
are available.  Fill prices are based on the mid of the last known quote.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional

from .broker_interface import (
    AccountInfo,
    BrokerInterface,
    OptionChain,
    OptionQuote,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Quote,
)

logger = logging.getLogger(__name__)


class PaperBroker(BrokerInterface):
    """
    Local paper broker backed by yfinance quotes.

    All fills are simulated at mid-price + a configurable slippage.
    This broker exists for running strategies without any API credentials.
    For realistic paper trading, use the real broker adapters with their
    paper/sandbox endpoints.
    """

    def __init__(
        self,
        starting_equity: float = 100_000.0,
        slippage_per_contract: float = 0.05,
    ):
        self._equity = Decimal(str(starting_equity))
        self._cash = Decimal(str(starting_equity))
        self._slippage = Decimal(str(slippage_per_contract))
        self._positions: Dict[str, Position] = {}
        self._orders: Dict[str, OrderResult] = {}
        self._data_source = None  # set by caller: YFinanceDataSource instance

        logger.info(
            "PaperBroker initialised | equity=%.2f | slippage_per_contract=%.2f",
            starting_equity,
            slippage_per_contract,
        )

    def set_data_source(self, data_source):
        self._data_source = data_source

    def verify_paper_endpoint(self) -> tuple:
        return True, "in-process paper broker — always paper"

    # ── Account ───────────────────────────────────────────────────────────────

    async def get_account(self) -> AccountInfo:
        # Equity = cash + mark-to-cost of open positions (no live pricing in paper mode)
        position_value = sum(
            p.avg_cost * p.quantity * 100 for p in self._positions.values()
        )
        equity = self._cash + position_value
        return AccountInfo(
            account_id="paper-local",
            equity=equity,
            cash=self._cash,
            buying_power=self._cash,
            is_paper=True,
        )

    # ── Positions ─────────────────────────────────────────────────────────────

    async def get_positions(self) -> List[Position]:
        return list(self._positions.values())

    # ── Quotes ────────────────────────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> Quote:
        if self._data_source is None:
            raise RuntimeError("PaperBroker requires a data_source for quotes.")
        price = await self._data_source.get_latest_price(symbol)
        p = Decimal(str(price))
        return Quote(
            symbol=symbol,
            bid=p * Decimal("0.9995"),
            ask=p * Decimal("1.0005"),
            last=p,
            volume=0,
            timestamp=datetime.utcnow(),
        )

    async def get_option_chain(self, symbol: str, expiration: date) -> OptionChain:
        if self._data_source is None:
            raise RuntimeError("PaperBroker requires a data_source for option chains.")
        return await self._data_source.get_option_chain(symbol, expiration)

    async def get_option_quote(self, option_symbol: str) -> OptionQuote:
        raise NotImplementedError(
            "PaperBroker cannot quote individual option contracts. "
            "Use get_option_chain instead."
        )

    # ── Orders ────────────────────────────────────────────────────────────────

    async def place_option_order(self, request: OrderRequest) -> OrderResult:
        if request.order_type == OrderType.MARKET:
            raise ValueError("Market orders for options are not allowed.")

        fill_price = request.limit_price + self._slippage
        cost = fill_price * request.quantity * 100  # 1 contract = 100 shares

        order_id = str(uuid.uuid4())

        if request.side in (OrderSide.BUY, OrderSide.BUY_TO_OPEN):
            if cost > self._cash:
                result = OrderResult(
                    order_id=order_id,
                    status=OrderStatus.REJECTED,
                    symbol=request.symbol,
                    option_symbol=request.option_symbol,
                    side=request.side,
                    quantity=request.quantity,
                    limit_price=request.limit_price,
                    cancel_reason="Insufficient buying power",
                    submitted_at=datetime.utcnow(),
                )
                logger.warning("Paper order rejected: insufficient cash | %s", request)
                self._orders[order_id] = result
                return result
            self._cash -= cost
            self._positions[request.option_symbol] = Position(
                symbol=request.symbol,
                quantity=request.quantity,
                avg_cost=fill_price,
                current_price=fill_price,
                market_value=fill_price * request.quantity * 100,
                unrealized_pnl=Decimal("0"),
                is_option=True,
                option_symbol=request.option_symbol,
            )
        else:
            # Sell / close
            pos = self._positions.pop(request.option_symbol, None)
            self._cash += fill_price * request.quantity * 100

        now = datetime.utcnow()
        result = OrderResult(
            order_id=order_id,
            status=OrderStatus.FILLED,
            symbol=request.symbol,
            option_symbol=request.option_symbol,
            side=request.side,
            quantity=request.quantity,
            limit_price=request.limit_price,
            filled_price=fill_price,
            filled_quantity=request.quantity,
            submitted_at=now,
            filled_at=now,
        )
        self._orders[order_id] = result
        logger.info(
            "Paper order filled | id=%s | %s %d %s @ %.2f",
            order_id,
            request.side.value,
            request.quantity,
            request.option_symbol,
            fill_price,
        )
        return result

    async def cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if order and order.status == OrderStatus.OPEN:
            order.status = OrderStatus.CANCELLED
            return True
        return False

    async def get_order_status(self, order_id: str) -> OrderResult:
        if order_id not in self._orders:
            raise LookupError(f"Order {order_id} not found")
        return self._orders[order_id]

    async def get_orders(
        self,
        status: Optional[OrderStatus] = None,
        limit: int = 100,
    ) -> List[OrderResult]:
        orders = list(self._orders.values())
        if status:
            orders = [o for o in orders if o.status == status]
        return orders[-limit:]

    async def get_available_expirations(self, symbol: str) -> List[date]:
        if self._data_source is None:
            raise RuntimeError("PaperBroker requires a data_source for expirations.")
        return await self._data_source.get_available_expirations(symbol)
