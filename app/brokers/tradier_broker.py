"""
Tradier broker adapter.

Tradier offers a REST API with excellent options data including Greeks,
IV, and full chains.  Uses the standard requests/httpx pattern.

Sandbox endpoint:  https://sandbox.tradier.com/v1
Live endpoint:     https://api.tradier.com/v1

WARNING: Do NOT point TRADIER_BASE_URL at the live endpoint unless
LIVE_TRADING_ENABLED=true is explicitly set.

Reference:  https://documentation.tradier.com/brokerage-api
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

import httpx

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

_TRADIER_STATUS_MAP = {
    "open": OrderStatus.OPEN,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "expired": OrderStatus.EXPIRED,
    "rejected": OrderStatus.REJECTED,
    "pending": OrderStatus.PENDING,
}


class TradierBroker(BrokerInterface):
    """
    Tradier Markets adapter.

    Tradier is particularly strong for options because it provides
    full chains with Greeks, IV rank, and tight bid/ask spreads.

    This is a *placeholder* with method stubs that map Tradier's REST
    API responses to the shared BrokerInterface types.
    """

    def __init__(self, access_token: str, base_url: str, is_paper: bool = True):
        self._token = access_token
        self._base_url = base_url.rstrip("/")
        self._is_paper = is_paper
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            },
            timeout=10.0,
        )
        logger.info(
            "TradierBroker initialised | paper=%s | base_url=%s",
            is_paper,
            base_url,
        )
        if not is_paper:
            logger.warning(
                "LIVE TRADING MODE: TradierBroker is connected to the live endpoint."
            )

    def verify_paper_endpoint(self) -> tuple:
        """
        Check URL hostname independently.
        Paper/sandbox: base_url contains 'sandbox'.
        Live:          base_url contains 'api.tradier.com'.
        """
        if "sandbox" in self._base_url.lower():
            return True, f"url=sandbox base_url={self._base_url}"
        return (
            False,
            f"base_url={self._base_url!r} does not contain 'sandbox' "
            f"(expected https://sandbox.tradier.com/v1)",
        )

    # ── Account ───────────────────────────────────────────────────────────────

    async def get_account(self) -> AccountInfo:
        resp = await self._client.get("/accounts")
        resp.raise_for_status()
        accounts = resp.json()["accounts"]["account"]
        if isinstance(accounts, dict):
            accounts = [accounts]
        acct = accounts[0]
        balances_resp = await self._client.get(f"/accounts/{acct['account_number']}/balances")
        balances_resp.raise_for_status()
        bal = balances_resp.json()["balances"]
        return AccountInfo(
            account_id=acct["account_number"],
            equity=Decimal(str(bal.get("total_equity", 0))),
            cash=Decimal(str(bal.get("cash", {}).get("cash_available", 0))),
            buying_power=Decimal(str(bal.get("option_buying_power", 0))),
            is_paper=self._is_paper,
        )

    # ── Positions ─────────────────────────────────────────────────────────────

    async def get_positions(self) -> List[Position]:
        acct = await self._get_account_number()
        resp = await self._client.get(f"/accounts/{acct}/positions")
        resp.raise_for_status()
        raw = resp.json().get("positions", {})
        if raw == "null" or raw is None:
            return []
        items = raw.get("position", [])
        if isinstance(items, dict):
            items = [items]
        positions = []
        for p in items:
            current = Decimal(str(p.get("cost_basis", 0)))
            positions.append(
                Position(
                    symbol=p["symbol"],
                    quantity=int(p["quantity"]),
                    avg_cost=Decimal(str(p.get("cost_basis", 0))) / max(int(p["quantity"]), 1),
                    current_price=current,
                    market_value=current,
                    unrealized_pnl=Decimal("0"),
                    is_option=len(p["symbol"]) > 6,
                )
            )
        return positions

    # ── Quotes ────────────────────────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> Quote:
        resp = await self._client.get("/markets/quotes", params={"symbols": symbol})
        resp.raise_for_status()
        q = resp.json()["quotes"]["quote"]
        return Quote(
            symbol=symbol,
            bid=Decimal(str(q["bid"])),
            ask=Decimal(str(q["ask"])),
            last=Decimal(str(q["last"])),
            volume=int(q.get("volume", 0)),
            timestamp=datetime.utcnow(),
        )

    async def get_option_chain(self, symbol: str, expiration: date) -> OptionChain:
        exp_str = expiration.strftime("%Y-%m-%d")
        resp = await self._client.get(
            "/markets/options/chains",
            params={"symbol": symbol, "expiration": exp_str, "greeks": "true"},
        )
        resp.raise_for_status()
        options = resp.json().get("options", {}).get("option", [])
        if isinstance(options, dict):
            options = [options]

        underlying_resp = await self.get_quote(symbol)
        chain = OptionChain(
            symbol=symbol,
            expiration=expiration,
            underlying_price=underlying_resp.mid,
            fetched_at=datetime.utcnow(),
        )
        for opt in options:
            greeks = opt.get("greeks") or {}
            contract = OptionContract(
                symbol=symbol,
                option_symbol=opt["symbol"],
                expiration=expiration,
                strike=Decimal(str(opt["strike"])),
                option_type=opt["option_type"],
                bid=Decimal(str(opt.get("bid", 0))),
                ask=Decimal(str(opt.get("ask", 0))),
                last=Decimal(str(opt.get("last", 0))),
                volume=int(opt.get("volume", 0)),
                open_interest=int(opt.get("open_interest", 0)),
                implied_volatility=float(opt.get("greeks", {}).get("smv_vol", 0) or 0),
                delta=greeks.get("delta"),
                gamma=greeks.get("gamma"),
                theta=greeks.get("theta"),
                vega=greeks.get("vega"),
            )
            if opt["option_type"] == "call":
                chain.calls.append(contract)
            else:
                chain.puts.append(contract)
        return chain

    async def get_option_quote(self, option_symbol: str) -> OptionQuote:
        resp = await self._client.get(
            "/markets/quotes",
            params={"symbols": option_symbol, "greeks": "true"},
        )
        resp.raise_for_status()
        q = resp.json()["quotes"]["quote"]
        greeks = q.get("greeks") or {}
        return OptionQuote(
            option_symbol=option_symbol,
            bid=Decimal(str(q.get("bid", 0))),
            ask=Decimal(str(q.get("ask", 0))),
            last=Decimal(str(q.get("last", 0))),
            volume=int(q.get("volume", 0)),
            open_interest=int(q.get("open_interest", 0)),
            implied_volatility=float(greeks.get("smv_vol", 0) or 0),
            delta=greeks.get("delta"),
            timestamp=datetime.utcnow(),
        )

    # ── Orders ────────────────────────────────────────────────────────────────

    async def place_option_order(self, request: OrderRequest) -> OrderResult:
        if request.order_type == OrderType.MARKET:
            raise ValueError("Market orders for options are not allowed. Use limit orders.")

        acct = await self._get_account_number()
        # Tradier uses different side strings for options
        side_map = {
            OrderSide.BUY_TO_OPEN: "buy_to_open",
            OrderSide.BUY_TO_CLOSE: "buy_to_close",
            OrderSide.SELL_TO_OPEN: "sell_to_open",
            OrderSide.SELL_TO_CLOSE: "sell_to_close",
            OrderSide.BUY: "buy_to_open",
            OrderSide.SELL: "sell_to_close",
        }
        payload = {
            "class": "option",
            "symbol": request.symbol,
            "option_symbol": request.option_symbol,
            "side": side_map[request.side],
            "quantity": str(request.quantity),
            "type": "limit",
            "duration": request.time_in_force,
            "price": str(request.limit_price),
        }
        logger.info("Placing Tradier option order | payload=%s", payload)

        resp = await self._client.post(f"/accounts/{acct}/orders", data=payload)
        resp.raise_for_status()
        data = resp.json()["order"]
        return OrderResult(
            order_id=str(data["id"]),
            status=_TRADIER_STATUS_MAP.get(data.get("status", "pending"), OrderStatus.PENDING),
            symbol=request.symbol,
            option_symbol=request.option_symbol,
            side=request.side,
            quantity=request.quantity,
            limit_price=request.limit_price,
            submitted_at=datetime.utcnow(),
        )

    async def cancel_order(self, order_id: str) -> bool:
        acct = await self._get_account_number()
        resp = await self._client.delete(f"/accounts/{acct}/orders/{order_id}")
        if resp.status_code == 200:
            logger.info("Tradier order cancelled | order_id=%s", order_id)
            return True
        return False

    async def get_order_status(self, order_id: str) -> OrderResult:
        acct = await self._get_account_number()
        resp = await self._client.get(f"/accounts/{acct}/orders/{order_id}")
        resp.raise_for_status()
        data = resp.json()["order"]
        return OrderResult(
            order_id=str(data["id"]),
            status=_TRADIER_STATUS_MAP.get(data.get("status", "pending"), OrderStatus.PENDING),
            symbol=data.get("symbol", ""),
            option_symbol=data.get("option_symbol", data.get("symbol", "")),
            side=OrderSide(data.get("side", "buy")),
            quantity=int(data.get("quantity", 0)),
            limit_price=Decimal(str(data.get("price", 0))),
            filled_price=Decimal(str(data["avg_fill_price"])) if data.get("avg_fill_price") else None,
            filled_quantity=int(data.get("exec_quantity", 0)),
        )

    async def get_available_expirations(self, symbol: str) -> List[date]:
        resp = await self._client.get(
            "/markets/options/expirations",
            params={"symbol": symbol},
        )
        resp.raise_for_status()
        raw = resp.json().get("expirations", {}).get("date", [])
        if isinstance(raw, str):
            raw = [raw]
        return sorted(date.fromisoformat(d) for d in raw)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _get_account_number(self) -> str:
        resp = await self._client.get("/accounts")
        resp.raise_for_status()
        accounts = resp.json()["accounts"]["account"]
        if isinstance(accounts, dict):
            accounts = [accounts]
        return accounts[0]["account_number"]

    async def close(self):
        await self._client.aclose()
