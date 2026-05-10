"""
Alpaca broker adapter.

Uses the Alpaca Markets REST API v2 for paper and live trading.
Options order flow requires the alpaca-py SDK.

Install:  pip install alpaca-py

Paper trading endpoint: https://paper-api.alpaca.markets
Live trading endpoint:  https://api.alpaca.markets

WARNING: Do NOT point ALPACA_BASE_URL at the live endpoint unless
LIVE_TRADING_ENABLED=true is explicitly set and you accept the risk.
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


class AlpacaBroker(BrokerInterface):
    """
    Alpaca Markets adapter.

    Currently a *placeholder* with method stubs.  Fill in each method body
    using the alpaca-py SDK once you have API credentials.

    Reference docs:
      https://docs.alpaca.markets/reference/
      https://alpaca.markets/sdks/python/
    """

    def __init__(self, api_key: str, secret_key: str, base_url: str, is_paper: bool = True):
        self._api_key = api_key
        self._secret_key = secret_key
        self._base_url = base_url.rstrip("/")
        self._is_paper = is_paper
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "APCA-API-KEY-ID": self._api_key,
                "APCA-API-SECRET-KEY": self._secret_key,
            },
            timeout=10.0,
        )
        logger.info(
            "AlpacaBroker initialised | paper=%s | base_url=%s",
            is_paper,
            base_url,
        )
        if not is_paper:
            logger.warning(
                "LIVE TRADING MODE: AlpacaBroker is connected to the live endpoint."
            )

    # ── Account ───────────────────────────────────────────────────────────────

    async def get_account(self) -> AccountInfo:
        resp = await self._client.get("/v2/account")
        resp.raise_for_status()
        data = resp.json()
        return AccountInfo(
            account_id=data["id"],
            equity=Decimal(data["equity"]),
            cash=Decimal(data["cash"]),
            buying_power=Decimal(data["buying_power"]),
            day_trade_count=int(data.get("daytrade_count", 0)),
            is_paper=self._is_paper,
        )

    # ── Positions ─────────────────────────────────────────────────────────────

    async def get_positions(self) -> List[Position]:
        resp = await self._client.get("/v2/positions")
        resp.raise_for_status()
        positions = []
        for p in resp.json():
            positions.append(
                Position(
                    symbol=p["symbol"],
                    quantity=int(p["qty"]),
                    avg_cost=Decimal(p["avg_entry_price"]),
                    current_price=Decimal(p["current_price"]),
                    market_value=Decimal(p["market_value"]),
                    unrealized_pnl=Decimal(p["unrealized_pl"]),
                    is_option=p.get("asset_class") == "us_option",
                    option_symbol=p.get("symbol") if p.get("asset_class") == "us_option" else None,
                )
            )
        return positions

    # ── Quotes ────────────────────────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> Quote:
        resp = await self._client.get(f"/v2/stocks/{symbol}/quotes/latest")
        resp.raise_for_status()
        q = resp.json()["quote"]
        return Quote(
            symbol=symbol,
            bid=Decimal(str(q["bp"])),
            ask=Decimal(str(q["ap"])),
            last=Decimal(str(q.get("ap", q["bp"]))),
            volume=int(q.get("bs", 0)),
            timestamp=datetime.fromisoformat(q["t"].replace("Z", "+00:00")),
        )

    async def get_option_chain(self, symbol: str, expiration: date) -> OptionChain:
        # TODO: Alpaca options chain endpoint
        # https://docs.alpaca.markets/reference/optioncontracts-1
        exp_str = expiration.strftime("%Y-%m-%d")
        resp = await self._client.get(
            "/v2/options/contracts",
            params={
                "underlying_symbols": symbol,
                "expiration_date": exp_str,
                "limit": 1000,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        chain = OptionChain(
            symbol=symbol,
            expiration=expiration,
            underlying_price=Decimal("0"),  # fetch separately if needed
            fetched_at=datetime.utcnow(),
        )
        for c in data.get("option_contracts", []):
            strike = Decimal(str(c["strike_price"]))
            contract = OptionContract(
                symbol=symbol,
                option_symbol=c["symbol"],
                expiration=expiration,
                strike=strike,
                option_type=c["type"],
                bid=Decimal(str(c.get("bid_price", 0))),
                ask=Decimal(str(c.get("ask_price", 0))),
                last=Decimal(str(c.get("close_price", 0))),
                volume=int(c.get("volume", 0)),
                open_interest=int(c.get("open_interest", 0)),
                implied_volatility=float(c.get("implied_volatility", 0)),
                delta=c.get("delta"),
                gamma=c.get("gamma"),
                theta=c.get("theta"),
                vega=c.get("vega"),
            )
            if c["type"] == "call":
                chain.calls.append(contract)
            else:
                chain.puts.append(contract)
        return chain

    async def get_option_quote(self, option_symbol: str) -> OptionQuote:
        resp = await self._client.get(f"/v2/options/snapshots/{option_symbol}")
        resp.raise_for_status()
        snap = resp.json()["snapshots"][option_symbol]
        greeks = snap.get("greeks", {})
        latest = snap.get("latestQuote", {})
        return OptionQuote(
            option_symbol=option_symbol,
            bid=Decimal(str(latest.get("bp", 0))),
            ask=Decimal(str(latest.get("ap", 0))),
            last=Decimal(str(snap.get("latestTrade", {}).get("p", 0))),
            volume=int(snap.get("dailyBar", {}).get("v", 0)),
            open_interest=int(snap.get("openInterest", 0)),
            implied_volatility=float(snap.get("impliedVolatility", 0)),
            delta=greeks.get("delta"),
            timestamp=datetime.utcnow(),
        )

    # ── Orders ────────────────────────────────────────────────────────────────

    async def place_option_order(self, request: OrderRequest) -> OrderResult:
        if request.order_type == OrderType.MARKET:
            raise ValueError("Market orders for options are not allowed. Use limit orders.")

        payload = {
            "symbol": request.option_symbol,
            "qty": str(request.quantity),
            "side": request.side.value,
            "type": "limit",
            "time_in_force": request.time_in_force,
            "limit_price": str(request.limit_price),
            "order_class": "simple",
        }
        logger.info("Placing option order | payload=%s", payload)

        resp = await self._client.post("/v2/orders", json=payload)
        resp.raise_for_status()
        data = resp.json()

        return OrderResult(
            order_id=data["id"],
            status=OrderStatus(data["status"]),
            symbol=request.symbol,
            option_symbol=request.option_symbol,
            side=request.side,
            quantity=request.quantity,
            limit_price=request.limit_price,
            submitted_at=datetime.fromisoformat(
                data["submitted_at"].replace("Z", "+00:00")
            ) if data.get("submitted_at") else None,
        )

    async def cancel_order(self, order_id: str) -> bool:
        resp = await self._client.delete(f"/v2/orders/{order_id}")
        if resp.status_code == 204:
            logger.info("Order cancelled | order_id=%s", order_id)
            return True
        logger.warning("Cancel failed | order_id=%s | status=%s", order_id, resp.status_code)
        return False

    async def get_order_status(self, order_id: str) -> OrderResult:
        resp = await self._client.get(f"/v2/orders/{order_id}")
        resp.raise_for_status()
        data = resp.json()
        return OrderResult(
            order_id=data["id"],
            status=OrderStatus(data["status"]),
            symbol=data.get("symbol", ""),
            option_symbol=data.get("symbol", ""),
            side=OrderSide(data["side"]),
            quantity=int(data["qty"]),
            limit_price=Decimal(str(data.get("limit_price") or 0)),
            filled_price=Decimal(str(data["filled_avg_price"])) if data.get("filled_avg_price") else None,
            filled_quantity=int(data.get("filled_qty", 0)),
            submitted_at=datetime.fromisoformat(
                data["submitted_at"].replace("Z", "+00:00")
            ) if data.get("submitted_at") else None,
            filled_at=datetime.fromisoformat(
                data["filled_at"].replace("Z", "+00:00")
            ) if data.get("filled_at") else None,
        )

    async def get_available_expirations(self, symbol: str) -> List[date]:
        resp = await self._client.get(
            "/v2/options/contracts",
            params={"underlying_symbols": symbol, "limit": 1000},
        )
        resp.raise_for_status()
        exps = set()
        for c in resp.json().get("option_contracts", []):
            exps.add(date.fromisoformat(c["expiration_date"]))
        return sorted(exps)

    async def close(self):
        await self._client.aclose()
