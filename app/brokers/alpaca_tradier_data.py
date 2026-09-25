"""Alpaca paper execution with read-only Tradier production options quotes."""
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlparse

import httpx

from .alpaca_broker import AlpacaBroker
from .broker_interface import OptionQuote, OptionContract, OptionChain
from app.trading.quote_evidence import quote_is_fresh, valid_quote


class AlpacaTradierDataBroker(AlpacaBroker):
    """Keep account, contract eligibility, calendar and orders on Alpaca paper."""

    def __init__(self, *, market_data_token: str, **kwargs):
        if (not kwargs.get("is_paper", True)
                or kwargs.get("base_url", "").rstrip("/") != "https://paper-api.alpaca.markets"
                or not kwargs.get("api_key", "").upper().startswith("PK")):
            raise ValueError("Tradier options data requires verified Alpaca paper execution")
        if not market_data_token or not market_data_token.strip():
            raise ValueError("TRADIER_MARKET_DATA_TOKEN is required")
        super().__init__(**kwargs)
        self._options_feed = "tradier_opra"
        self._tradier_data = httpx.AsyncClient(
            base_url="https://api.tradier.com/v1/",
            headers={"Authorization": "Bearer " + market_data_token.strip(), "Accept": "application/json"},
            timeout=10.0, follow_redirects=False,
            event_hooks={"request": [self._check_data_request]},
        )

    @staticmethod
    async def _check_data_request(request):
        url = urlparse(str(request.url))
        if (request.method != "GET" or url.scheme != "https"
                or url.netloc != "api.tradier.com" or url.path != "/v1/markets/quotes"):
            raise ValueError("Tradier client permits only the production quotes GET endpoint")

    @staticmethod
    def _timestamp(q, now):
        # Both sides must be dated. Use the older side so one fresh side cannot
        # conceal a stale opposite side. Never replace missing dates with now.
        try:
            times = [datetime.fromtimestamp(float(q[k]) / 1000, timezone.utc)
                     for k in ("bid_date", "ask_date")]
            if any(t > now for t in times):
                return None
            return min(times)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            return None

    async def _fetch_snapshots(self, symbols):
        snapshots = {}
        for i in range(0, len(symbols), 50):
            requested = set(symbols[i:i + 50])
            response = await self._tradier_data.get("markets/quotes", params={
                "symbols": ",".join(sorted(requested)), "greeks": "true",
            })
            response.raise_for_status()
            rows = (response.json().get("quotes") or {}).get("quote") or []
            if isinstance(rows, dict):
                rows = [rows]
            if not isinstance(rows, list):
                raise ValueError("Invalid Tradier quotes response")
            now = datetime.now(timezone.utc)
            for q in rows:
                if not isinstance(q, dict) or q.get("symbol") not in requested:
                    continue
                try:
                    bid, ask = float(q["bid"]), float(q["ask"])
                    ts = self._timestamp(q, now)
                    if not valid_quote(bid, ask) or not quote_is_fresh(ts, now):
                        continue
                    greeks = q.get("greeks") or {}
                    snapshots[q["symbol"]] = {
                        "latestQuote": {"bp": bid, "ap": ask, "t": ts.isoformat()},
                        "latestTrade": {"p": q.get("last") or 0},
                        "dailyBar": {"v": q.get("volume") or 0},
                        "openInterest": q.get("open_interest") or 0,
                        "greeks": greeks,
                        "impliedVolatility": greeks.get("smv_vol") or 0,
                    }
                except (KeyError, ValueError, TypeError):
                    continue
        return snapshots

    async def get_option_chain(self, symbol, expiration):
        contracts = await self._get_option_contracts({
            "underlying_symbols": symbol, "expiration_date": expiration.isoformat(),
        })
        contracts = [c for c in contracts if c.get("tradable") is True]
        snapshots = await self._fetch_snapshots([c["symbol"] for c in contracts])
        underlying = await self.get_quote(symbol)
        chain = OptionChain(symbol, expiration, underlying.mid,
                            fetched_at=datetime.now(timezone.utc))
        for c in contracts:
            snap = snapshots.get(c["symbol"])
            if snap is None:
                continue
            q, greeks = snap["latestQuote"], snap["greeks"]
            ts = datetime.fromisoformat(q["t"])
            if not quote_is_fresh(ts, datetime.now(timezone.utc)):
                continue
            contract = OptionContract(
                symbol=symbol, option_symbol=c["symbol"], expiration=expiration,
                strike=Decimal(str(c["strike_price"])), option_type=c["type"],
                bid=Decimal(str(q["bp"])), ask=Decimal(str(q["ap"])),
                last=Decimal(str(snap["latestTrade"]["p"])), volume=int(snap["dailyBar"]["v"]),
                open_interest=int(snap["openInterest"]),
                implied_volatility=float(snap["impliedVolatility"]),
                delta=greeks.get("delta"), gamma=greeks.get("gamma"),
                theta=greeks.get("theta"), vega=greeks.get("vega"),
                quote_timestamp=ts, quote_feed="tradier_opra",
            )
            if c["type"] == "call":
                chain.calls.append(contract)
            elif c["type"] == "put":
                chain.puts.append(contract)
        return chain

    async def get_option_quote(self, option_symbol):
        snapshots = await self._fetch_snapshots([option_symbol])
        snap = snapshots.get(option_symbol)
        if snap is None:
            raise ValueError("No fresh valid Tradier quote for " + option_symbol)
        q, greeks = snap["latestQuote"], snap["greeks"]
        return OptionQuote(
            option_symbol=option_symbol, bid=Decimal(str(q["bp"])), ask=Decimal(str(q["ap"])),
            last=Decimal(str(snap["latestTrade"]["p"])), volume=int(snap["dailyBar"]["v"]),
            open_interest=int(snap["openInterest"]), implied_volatility=float(snap["impliedVolatility"]),
            delta=greeks.get("delta"), timestamp=datetime.fromisoformat(q["t"]), feed="tradier_opra",
        )

    async def close(self):
        try:
            await self._tradier_data.aclose()
        finally:
            await super().close()
