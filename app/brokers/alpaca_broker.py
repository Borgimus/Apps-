"""
Alpaca broker adapter.

Alpaca uses two separate hosts:
  Trading API  : paper-api.alpaca.markets  (account, orders, positions)
  Market Data  : data.alpaca.markets       (quotes, bars, option chains)

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

    # Market data always served from this host regardless of paper/live
    _DATA_URL = "https://data.alpaca.markets"

    def __init__(self, api_key: str, secret_key: str, base_url: str, is_paper: bool = True):
        self._api_key = api_key
        self._secret_key = secret_key
        self._base_url = base_url.rstrip("/")
        self._is_paper = is_paper
        _headers = {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._secret_key,
        }
        # Trading client — account, orders, positions
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=_headers,
            timeout=10.0,
        )
        # Market data client — quotes, bars, option chains
        self._data_client = httpx.AsyncClient(
            base_url=self._DATA_URL,
            headers=_headers,
            timeout=10.0,
        )
        logger.info(
            "AlpacaBroker initialised | paper=%s | trade_url=%s | data_url=%s",
            is_paper,
            base_url,
            self._DATA_URL,
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
        # Equity quotes come from the data host, not the trading host
        resp = await self._data_client.get(f"/v2/stocks/{symbol}/quotes/latest")
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
        # Contract metadata lives on the trading host; snapshots on data host
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

        # Fetch underlying price for context
        try:
            underlying_quote = await self.get_quote(symbol)
            underlying_price = underlying_quote.mid
        except Exception:
            underlying_price = Decimal("0")

        contracts_raw = data.get("option_contracts", [])

        # Enrich with live quotes from the snapshot endpoint
        all_symbols = [c["symbol"] for c in contracts_raw]
        snapshots = await self._fetch_snapshots(all_symbols)

        chain = OptionChain(
            symbol=symbol,
            expiration=expiration,
            underlying_price=underlying_price,
            fetched_at=datetime.utcnow(),
        )
        for c in contracts_raw:
            snap = snapshots.get(c["symbol"], {})
            greeks = snap.get("greeks", {})
            latest_quote = snap.get("latestQuote", {})
            contract = OptionContract(
                symbol=symbol,
                option_symbol=c["symbol"],
                expiration=expiration,
                strike=Decimal(str(c["strike_price"])),
                option_type=c["type"],
                bid=Decimal(str(latest_quote.get("bp") or c.get("bid_price") or 0)),
                ask=Decimal(str(latest_quote.get("ap") or c.get("ask_price") or 0)),
                last=Decimal(str(snap.get("latestTrade", {}).get("p") or c.get("close_price") or 0)),
                volume=int(snap.get("dailyBar", {}).get("v") or c.get("volume") or 0),
                open_interest=int(c.get("open_interest") or 0),
                implied_volatility=float(snap.get("impliedVolatility") or c.get("implied_volatility") or 0),
                delta=greeks.get("delta") or c.get("delta"),
                gamma=greeks.get("gamma") or c.get("gamma"),
                theta=greeks.get("theta") or c.get("theta"),
                vega=greeks.get("vega") or c.get("vega"),
            )
            if c["type"] == "call":
                chain.calls.append(contract)
            else:
                chain.puts.append(contract)
        return chain

    async def get_option_quote(self, option_symbol: str) -> OptionQuote:
        # Option snapshots (bid/ask/greeks) live on the data host under v1beta1
        resp = await self._data_client.get(
            "/v1beta1/options/snapshots",
            params={"symbols": option_symbol},
        )
        resp.raise_for_status()
        snap = resp.json().get("snapshots", {}).get(option_symbol, {})
        greeks = snap.get("greeks", {})
        latest = snap.get("latestQuote", {})
        return OptionQuote(
            option_symbol=option_symbol,
            bid=Decimal(str(latest.get("bp") or 0)),
            ask=Decimal(str(latest.get("ap") or 0)),
            last=Decimal(str(snap.get("latestTrade", {}).get("p") or 0)),
            volume=int(snap.get("dailyBar", {}).get("v") or 0),
            open_interest=int(snap.get("openInterest") or 0),
            implied_volatility=float(snap.get("impliedVolatility") or 0),
            delta=greeks.get("delta"),
            timestamp=datetime.utcnow(),
        )

    async def _fetch_snapshots(self, symbols: List[str]) -> dict:
        """Batch-fetch option snapshots, chunking to stay under URL length limits."""
        snapshots: dict = {}
        chunk_size = 100
        for i in range(0, len(symbols), chunk_size):
            chunk = symbols[i : i + chunk_size]
            resp = await self._data_client.get(
                "/v1beta1/options/snapshots",
                params={"symbols": ",".join(chunk)},
            )
            if resp.status_code == 200:
                snapshots.update(resp.json().get("snapshots", {}))
        return snapshots

    # ── Orders ────────────────────────────────────────────────────────────────

    async def place_option_order(self, request: OrderRequest) -> OrderResult:
        if request.order_type == OrderType.MARKET:
            raise ValueError("Market orders for options are not allowed. Use limit orders.")

        # Alpaca accepts only "buy" / "sell" — map buy_to_open/sell_to_close etc.
        alpaca_side = "buy" if request.side in (OrderSide.BUY, OrderSide.BUY_TO_OPEN) else "sell"
        payload = {
            "symbol": request.option_symbol,
            "qty": str(request.quantity),
            "side": alpaca_side,
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
            status=self._parse_status(data["status"]),
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
        return self._order_from_dict(data)

    async def get_orders(
        self,
        status: Optional["OrderStatus"] = None,
        limit: int = 100,
    ) -> List[OrderResult]:
        # Alpaca status param: "open" covers accepted/new/pending_new/held
        from .broker_interface import OrderStatus as OS
        if status and status in (OS.FILLED,):
            alpaca_status = "closed"
        else:
            alpaca_status = "open"

        resp = await self._client.get(
            "/v2/orders",
            params={"status": alpaca_status, "limit": min(limit, 500), "direction": "desc"},
        )
        resp.raise_for_status()
        results = []
        for item in resp.json():
            try:
                results.append(self._order_from_dict(item))
            except Exception as exc:
                logger.warning("Skipping unparseable order: %s", exc)
        return results

    def _order_from_dict(self, data: dict) -> OrderResult:
        """Parse a single Alpaca order dict into an OrderResult."""
        alpaca_side = data.get("side", "buy")
        side = OrderSide.BUY if alpaca_side == "buy" else OrderSide.SELL
        return OrderResult(
            order_id=data["id"],
            status=self._parse_status(data["status"]),
            symbol=data.get("symbol", ""),
            option_symbol=data.get("symbol", ""),
            side=side,
            quantity=int(data.get("qty", 0)),
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

    def _parse_status(self, status_str: str) -> "OrderStatus":
        """Map an Alpaca status string to OrderStatus, defaulting gracefully."""
        from .broker_interface import OrderStatus as OS
        try:
            return OS(status_str)
        except ValueError:
            logger.debug("Unknown Alpaca order status %r — defaulting to PENDING", status_str)
            return OS.PENDING

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
        await self._data_client.aclose()
