"""Alpaca market-data client: fetch daily bars and the latest trade, build a MarketSnapshot.

Records the feed (IEX/SIP) on every snapshot — Alpaca and TC2000 feeds are never assumed
identical. The HTTP transport is injected so parsing is testable without httpx or network.

send(method, url, headers, params) -> (status_code:int, json:dict)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from src.data.market_data import Bar, Feed, MarketSnapshot, Quote

Sender = Callable[[str, str, dict, dict | None], tuple[int, dict]]

DATA_HOST = "https://data.alpaca.markets"


def _parse_ts(s: str) -> datetime:
    # Alpaca timestamps are RFC3339, e.g. "2026-07-31T00:00:00Z".
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


class AlpacaDataClient:
    def __init__(self, key_id: str, secret_key: str, *, feed: str = "iex",
                 base_url: str = DATA_HOST, send: Sender | None = None):
        if not key_id or not secret_key:
            raise ValueError("data credentials required")
        self.feed = Feed(feed)
        self.base = base_url.rstrip("/")
        self._headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
        self._send = send or self._default_sender()

    @staticmethod
    def _default_sender() -> Sender:  # pragma: no cover - requires httpx + network
        import httpx

        client = httpx.Client(timeout=10.0)

        def send(method, url, headers, params):
            resp = client.request(method, url, headers=headers, params=params)
            try:
                body = resp.json()
            except Exception:
                body = {}
            return resp.status_code, body

        return send

    def _get(self, path: str, params: dict) -> dict:
        status, body = self._send("GET", f"{self.base}{path}", self._headers, params)
        if status >= 400:
            raise RuntimeError(f"alpaca-data GET {path} -> HTTP {status}")
        return body

    def get_daily_snapshot(self, symbol: str, *, limit: int = 250, adjusted: bool = True,
                           now: datetime | None = None) -> MarketSnapshot:
        """Fetch recent daily bars and build a MarketSnapshot (for indicators/decisions)."""
        body = self._get(
            f"/v2/stocks/{symbol}/bars",
            {"timeframe": "1Day", "limit": limit, "feed": self.feed.value,
             "adjustment": "split" if adjusted else "raw"},
        )
        bars = [
            Bar(ts=_parse_ts(b["t"]), open=float(b["o"]), high=float(b["h"]),
                low=float(b["l"]), close=float(b["c"]), volume=float(b["v"]))
            for b in body.get("bars", [])
        ]
        as_of = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
        return MarketSnapshot(symbol=symbol, as_of=as_of, feed=self.feed, adjusted=adjusted,
                              bars=bars, meta={"source": "alpaca"})

    def get_last_trade_price(self, symbol: str) -> float:
        body = self._get(f"/v2/stocks/{symbol}/trades/latest", {"feed": self.feed.value})
        return float(body["trade"]["p"])

    def get_latest_quote(self, symbol: str) -> Quote:
        body = self._get(f"/v2/stocks/{symbol}/quotes/latest", {"feed": self.feed.value})
        q = body["quote"]
        return Quote(ts=_parse_ts(q["t"]), bid=float(q["bp"]), ask=float(q["ap"]))
