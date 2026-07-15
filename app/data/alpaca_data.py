"""
Alpaca Markets bar data fetcher.

Supports US equities/ETFs via /v2/stocks and crypto via /v1beta3/crypto.
Forex and futures are not available on Alpaca — callers should fall back
to yfinance for those symbols.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

_DEFAULT_DATA_URL = "https://data.alpaca.markets"

_CRYPTO_SYMBOLS = {
    "BTCUSD", "ETHUSD", "SOLUSD", "AVAXUSD", "DOGEUSD",
    "LTCUSD", "BCHUSD", "LINKUSD", "UNIUSD", "AAVEUSD",
}

_FOREX_PREFIXES = {"EUR", "GBP", "USD", "JPY", "AUD", "CAD", "CHF", "NZD"}
_FUTURES_SUFFIXES = {"=F"}


def is_supported(symbol: str) -> bool:
    """Return True if Alpaca has data for this symbol (equity or crypto)."""
    sym = symbol.upper()
    if sym in _CRYPTO_SYMBOLS:
        return True
    # 6-char forex pairs (EURUSD, GBPUSD …)
    if len(sym) == 6 and sym[:3] in _FOREX_PREFIXES and sym[3:] in _FOREX_PREFIXES:
        return False
    if sym.endswith("=F"):
        return False
    return True


def _is_crypto(symbol: str) -> bool:
    return symbol.upper() in _CRYPTO_SYMBOLS


def fetch_alpaca_bars(
    symbol: str,
    api_key: str,
    secret_key: str,
    timeframe: str = "1Min",
    days: int = 7,
    data_feed: str = "iex",
    data_url: str = _DEFAULT_DATA_URL,
) -> pd.DataFrame:
    """
    Fetch OHLCV bars from Alpaca.

    Returns a UTC-indexed DataFrame with columns [open, high, low, close, volume],
    same shape as fetch_1min_bars(). Returns empty DataFrame on failure.

    Parameters
    ----------
    symbol    : Ticker (e.g. "SPY", "BTCUSD")
    api_key   : APCA-API-KEY-ID
    secret_key: APCA-API-SECRET-KEY
    timeframe : "1Min" | "5Min" | "15Min" | "1Hour" | "1Day"
    days      : History window in days
    data_feed : "iex" (free) or "sip" (paid subscription)
    data_url  : Override Alpaca data base URL
    """
    sym = symbol.upper()

    if not is_supported(sym):
        logger.debug("Symbol %s not supported by Alpaca data API", sym)
        return pd.DataFrame()

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
    }

    try:
        all_bars: list = []
        next_token: Optional[str] = None

        with httpx.Client(headers=headers, timeout=20.0) as client:
            if _is_crypto(sym):
                base_url = f"{data_url}/v1beta3/crypto/us/bars"
                base_params: dict = {
                    "symbols": sym,
                    "timeframe": timeframe,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "limit": 1000,
                    "sort": "asc",
                }
            else:
                base_url = f"{data_url}/v2/stocks/{sym}/bars"
                base_params = {
                    "timeframe": timeframe,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "limit": 1000,
                    "feed": data_feed,
                    "sort": "asc",
                }

            while True:
                params = {**base_params}
                if next_token:
                    params["page_token"] = next_token

                resp = client.get(base_url, params=params)
                resp.raise_for_status()
                payload = resp.json()

                if _is_crypto(sym):
                    chunk = payload.get("bars", {}).get(sym, [])
                else:
                    chunk = payload.get("bars", [])

                all_bars.extend(chunk)
                next_token = payload.get("next_page_token")
                if not next_token or len(all_bars) >= 2000:
                    break

        if not all_bars:
            logger.warning("Alpaca returned 0 bars for %s", sym)
            return pd.DataFrame()

        df = pd.DataFrame(all_bars)
        df = df.rename(columns={"t": "timestamp", "o": "open", "h": "high",
                                 "l": "low", "c": "close", "v": "volume"})
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp")
        keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[keep].sort_index().dropna()

        logger.info("Alpaca: fetched %d bars for %s (last=%s)",
                    len(df), sym, df.index[-1])
        return df

    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        body = exc.response.text[:300]
        logger.error("Alpaca HTTP %s for %s: %s", status, sym, body)
        return pd.DataFrame()
    except Exception as exc:
        logger.error("Alpaca fetch failed for %s: %s", sym, exc)
        return pd.DataFrame()
