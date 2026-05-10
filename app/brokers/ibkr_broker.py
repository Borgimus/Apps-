"""
Interactive Brokers adapter.

Connects via IB Gateway or Trader Workstation using the ib_insync library.
TWS must be running locally with the API enabled.

Install:  pip install ib_insync

Port configuration:
  Paper trading:  TWS = 7497,  IB Gateway = 4002
  Live trading:   TWS = 7496,  IB Gateway = 4001

WARNING: IB does not distinguish paper vs live by URL.  The port number is the
only indicator.  Verify IBKR_PORT before enabling live trading.

Reference:  https://ib-insync.readthedocs.io/
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from .broker_interface import (
    AccountInfo,
    BrokerInterface,
    OptionChain,
    OptionContract,
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

# Paper trading ports: 7497 (TWS), 4002 (Gateway)
# Live trading ports:  7496 (TWS), 4001 (Gateway)
_PAPER_PORTS = {7497, 4002}


class IBKRBroker(BrokerInterface):
    """
    Interactive Brokers adapter via ib_insync.

    This is a *placeholder*.  The ib_insync library uses a synchronous event
    loop pattern that must be run inside asyncio.  All blocking calls below
    must be wrapped with asyncio.get_event_loop().run_in_executor() or
    ib_insync's native async wrappers.

    Full implementation guidance:
      https://ib-insync.readthedocs.io/recipes.html
    """

    def __init__(self, host: str, port: int, client_id: int):
        self._host = host
        self._port = port
        self._client_id = client_id
        self._is_paper = port in _PAPER_PORTS
        self._ib = None  # ib_insync.IB() instance, lazily connected

        logger.info(
            "IBKRBroker configured | host=%s port=%d client_id=%d paper=%s",
            host,
            port,
            client_id,
            self._is_paper,
        )
        if not self._is_paper:
            logger.warning(
                "LIVE TRADING MODE: IBKRBroker is using a live port (%d).", port
            )

    async def _get_ib(self):
        """Lazily connect to TWS/Gateway."""
        if self._ib is not None:
            return self._ib
        try:
            import ib_insync
        except ImportError as e:
            raise ImportError(
                "ib_insync is not installed. Run: pip install ib_insync"
            ) from e

        self._ib = ib_insync.IB()
        await self._ib.connectAsync(self._host, self._port, clientId=self._client_id)
        logger.info("Connected to IBKR TWS/Gateway at %s:%d", self._host, self._port)
        return self._ib

    # ── Account ───────────────────────────────────────────────────────────────

    async def get_account(self) -> AccountInfo:
        ib = await self._get_ib()
        summary = {v.tag: v.value for v in ib.accountSummary()}
        return AccountInfo(
            account_id=summary.get("AccountCode", "unknown"),
            equity=Decimal(summary.get("NetLiquidation", "0")),
            cash=Decimal(summary.get("TotalCashValue", "0")),
            buying_power=Decimal(summary.get("OptionBuyingPower", "0")),
            is_paper=self._is_paper,
        )

    # ── Positions ─────────────────────────────────────────────────────────────

    async def get_positions(self) -> List[Position]:
        ib = await self._get_ib()
        positions = []
        for p in ib.positions():
            contract = p.contract
            is_opt = contract.secType == "OPT"
            positions.append(
                Position(
                    symbol=contract.symbol,
                    quantity=int(p.position),
                    avg_cost=Decimal(str(p.avgCost)),
                    current_price=Decimal(str(p.avgCost)),  # requires market data subscription
                    market_value=Decimal(str(p.avgCost)) * int(p.position),
                    unrealized_pnl=Decimal("0"),
                    is_option=is_opt,
                    option_symbol=contract.localSymbol if is_opt else None,
                )
            )
        return positions

    # ── Quotes ────────────────────────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> Quote:
        import ib_insync
        ib = await self._get_ib()
        contract = ib_insync.Stock(symbol, "SMART", "USD")
        ib.qualifyContracts(contract)
        ticker = ib.reqMktData(contract, snapshot=True)
        ib.sleep(1)
        return Quote(
            symbol=symbol,
            bid=Decimal(str(ticker.bid or 0)),
            ask=Decimal(str(ticker.ask or 0)),
            last=Decimal(str(ticker.last or 0)),
            volume=int(ticker.volume or 0),
            timestamp=datetime.utcnow(),
        )

    async def get_option_chain(self, symbol: str, expiration: date) -> OptionChain:
        import ib_insync
        ib = await self._get_ib()
        # Request option chain details
        underlying = ib_insync.Stock(symbol, "SMART", "USD")
        ib.qualifyContracts(underlying)
        chains = ib.reqSecDefOptParams(underlying.symbol, "", underlying.secType, underlying.conId)

        chain = OptionChain(
            symbol=symbol,
            expiration=expiration,
            underlying_price=Decimal("0"),
            fetched_at=datetime.utcnow(),
        )
        # Iterate strikes for this expiration and build OptionContract list
        # Full implementation requires reqContractDetails + reqMktData per strike
        logger.warning(
            "IBKRBroker.get_option_chain is a stub — full implementation requires "
            "per-strike market data subscriptions."
        )
        return chain

    async def get_option_quote(self, option_symbol: str) -> OptionQuote:
        # IBKR uses conId rather than OCC symbols; option_symbol must be localSymbol
        logger.warning("IBKRBroker.get_option_quote requires conId lookup — stub.")
        return OptionQuote(
            option_symbol=option_symbol,
            bid=Decimal("0"),
            ask=Decimal("0"),
            last=Decimal("0"),
            volume=0,
            open_interest=0,
            implied_volatility=0.0,
            delta=None,
            timestamp=datetime.utcnow(),
        )

    # ── Orders ────────────────────────────────────────────────────────────────

    async def place_option_order(self, request: OrderRequest) -> OrderResult:
        if request.order_type == OrderType.MARKET:
            raise ValueError("Market orders for options are not allowed. Use limit orders.")

        import ib_insync
        ib = await self._get_ib()

        # Parse option_symbol (OCC format: SPY230915C00420000)
        sym = request.option_symbol
        underlying = sym[:3].rstrip() if len(sym) > 15 else request.symbol
        logger.info("Placing IBKR option order | option_symbol=%s", sym)

        # Build a minimal Option contract — full impl must parse OCC symbol
        contract = ib_insync.Option(
            symbol=request.symbol,
            lastTradeDateOrContractMonth=sym[6:14] if len(sym) > 14 else "",
            strike=0.0,  # parse from OCC symbol
            right="C" if "C" in sym[14:15] else "P",
            exchange="SMART",
            currency="USD",
        )
        order = ib_insync.LimitOrder(
            action="BUY" if "buy" in request.side.value else "SELL",
            totalQuantity=request.quantity,
            lmtPrice=float(request.limit_price),
        )
        trade = ib.placeOrder(contract, order)
        return OrderResult(
            order_id=str(trade.order.orderId),
            status=OrderStatus.PENDING,
            symbol=request.symbol,
            option_symbol=request.option_symbol,
            side=request.side,
            quantity=request.quantity,
            limit_price=request.limit_price,
            submitted_at=datetime.utcnow(),
        )

    async def cancel_order(self, order_id: str) -> bool:
        ib = await self._get_ib()
        import ib_insync
        order = ib_insync.Order(orderId=int(order_id))
        ib.cancelOrder(order)
        logger.info("IBKR cancel requested | order_id=%s", order_id)
        return True

    async def get_order_status(self, order_id: str) -> OrderResult:
        ib = await self._get_ib()
        for trade in ib.trades():
            if str(trade.order.orderId) == order_id:
                status_map = {
                    "Submitted": OrderStatus.OPEN,
                    "Filled": OrderStatus.FILLED,
                    "Cancelled": OrderStatus.CANCELLED,
                    "Inactive": OrderStatus.EXPIRED,
                }
                return OrderResult(
                    order_id=order_id,
                    status=status_map.get(trade.orderStatus.status, OrderStatus.PENDING),
                    symbol=trade.contract.symbol,
                    option_symbol=trade.contract.localSymbol or trade.contract.symbol,
                    side=OrderSide.BUY if trade.order.action == "BUY" else OrderSide.SELL,
                    quantity=int(trade.order.totalQuantity),
                    limit_price=Decimal(str(trade.order.lmtPrice)),
                    filled_quantity=int(trade.orderStatus.filled),
                    filled_price=Decimal(str(trade.orderStatus.avgFillPrice)) if trade.orderStatus.filled else None,
                )
        raise LookupError(f"Order {order_id} not found in active trades")

    async def close(self):
        if self._ib is not None:
            self._ib.disconnect()
            self._ib = None
