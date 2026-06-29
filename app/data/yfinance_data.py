"""
Yahoo Finance data source — for RESEARCH and BACKTESTING only.

IMPORTANT DISCLAIMER
────────────────────
yfinance scrapes unofficial Yahoo Finance endpoints.  The data:
  • May be delayed, incorrect, or missing without notice.
  • Must NOT be used as the execution-grade source for placing real or paper
    orders.  Always use broker-provided quotes and chains for order flow.
  • Has incomplete options history; anything labeled "synthetic" here uses
    Black-Scholes estimates and is clearly marked as an approximation.

Use this module to:
  • Pull historical OHLCV bars for backtesting
  • Research option chain structure (not for execution)
  • Compute rough IV / Greeks for signal filtering in research mode
"""

from __future__ import annotations

import asyncio
import logging
import os
import warnings
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import httpx as _httpx
import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import norm

from ..brokers.broker_interface import OptionChain, OptionContract

logger = logging.getLogger(__name__)

_RESEARCH_WARNING = (
    "[yfinance] Data is unofficial and for research/educational use only. "
    "Do not use for execution."
)

_ALPACA_DATA_URL = "https://data.alpaca.markets"
_YF_TO_ALPACA_TF = {
    "1m": "1Min", "2m": "2Min", "5m": "5Min", "15m": "15Min",
    "30m": "30Min", "60m": "1Hour", "1h": "1Hour", "1d": "1Day",
}


def _make_alpaca_data_client() -> _httpx.Client:
    ca = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")
    verify = ca if ca and os.path.exists(ca) else True
    return _httpx.Client(
        base_url=_ALPACA_DATA_URL,
        headers={
            "APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ""),
            "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", ""),
            "Accept": "application/json",
        },
        verify=verify,
        timeout=30.0,
    )


def _fetch_alpaca_bars_sync(symbol: str, timeframe: str, start: str) -> pd.DataFrame:
    all_bars: list = []
    params: dict = {
        "symbols": symbol, "timeframe": timeframe,
        "start": start, "feed": "iex", "limit": 10000,
    }
    with _make_alpaca_data_client() as client:
        while True:
            resp = client.get("/v2/stocks/bars", params=params)
            resp.raise_for_status()
            data = resp.json()
            all_bars.extend(data.get("bars", {}).get(symbol, []))
            next_token = data.get("next_page_token")
            if not next_token:
                break
            params["page_token"] = next_token
    if not all_bars:
        return pd.DataFrame()
    df = pd.DataFrame(all_bars)
    df["t"] = pd.to_datetime(df["t"], utc=True)
    df = df.set_index("t")
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            df[col] = float("nan")
    return df[["open", "high", "low", "close", "volume"]]


class YFinanceDataSource:
    """
    Wraps yfinance with caching and clearly-marked approximation flags.
    All async methods run the blocking yfinance calls in a thread pool.
    """

    def __init__(self, cache_ttl_seconds: int = 300):
        self._cache_ttl = cache_ttl_seconds
        self._price_cache: Dict[str, Tuple[float, datetime]] = {}
        self._bar_cache: Dict[str, Tuple[pd.DataFrame, datetime]] = {}
        logger.debug("YFinanceDataSource initialised | cache_ttl=%ds", cache_ttl_seconds)

    # ── OHLCV bars ────────────────────────────────────────────────────────────

    async def get_bars(
        self,
        symbol: str,
        start: str | date,
        end: str | date | None = None,
        interval: str = "1d",
    ) -> pd.DataFrame:
        """
        Return OHLCV DataFrame for backtesting.

        Parameters
        ----------
        interval : "1m","2m","5m","15m","30m","60m","90m","1h","1d","5d","1wk","1mo"
                   Sub-daily intervals are limited to 60 days of history.
        """
        warnings.warn(_RESEARCH_WARNING, stacklevel=2)
        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(
            None, self._fetch_bars, symbol, str(start), str(end) if end else None, interval
        )
        return df

    def _fetch_bars(
        self, symbol: str, start: str, end: Optional[str], interval: str
    ) -> pd.DataFrame:
        ticker = yf.Ticker(symbol)
        kwargs = dict(start=start, interval=interval, auto_adjust=True)
        if end:
            kwargs["end"] = end
        df = ticker.history(**kwargs)
        if df.empty:
            logger.warning("yfinance returned empty DataFrame for %s", symbol)
            return df
        df.index = pd.to_datetime(df.index, utc=True)
        df.columns = [c.lower() for c in df.columns]
        logger.debug("Fetched %d bars for %s [%s]", len(df), symbol, interval)
        return df

    # ── Latest price ─────────────────────────────────────────────────────────

    async def get_latest_price(self, symbol: str) -> float:
        """Return most recent close price.  For research/paper use only."""
        warnings.warn(_RESEARCH_WARNING, stacklevel=2)
        now = datetime.utcnow()
        cached = self._price_cache.get(symbol)
        if cached and (now - cached[1]).total_seconds() < self._cache_ttl:
            return cached[0]

        loop = asyncio.get_event_loop()
        price = await loop.run_in_executor(None, self._fetch_latest_price, symbol)
        self._price_cache[symbol] = (price, now)
        return price

    def _fetch_latest_price(self, symbol: str) -> float:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d", interval="1m")
        if hist.empty:
            raise ValueError(f"No price data returned for {symbol}")
        return float(hist["Close"].iloc[-1])

    # ── Intraday bars ─────────────────────────────────────────────────────────

    async def get_intraday_bars(
        self, symbol: str, interval: str = "5m", days_back: int = 5
    ) -> pd.DataFrame:
        """Return intraday OHLCV bars via Alpaca Market Data API (IEX feed)."""
        start = str(date.today() - timedelta(days=days_back + 1))
        timeframe = _YF_TO_ALPACA_TF.get(interval, "5Min")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, _fetch_alpaca_bars_sync, symbol, timeframe, start
        )

    # ── Option chains (RESEARCH only) ─────────────────────────────────────────

    async def get_option_chain(
        self, symbol: str, expiration: date
    ) -> OptionChain:
        """
        Fetch option chain from Yahoo Finance.

        ⚠ APPROXIMATION WARNING: Greeks and IV from yfinance are unreliable.
        Use broker-provided chains for any execution decision.
        """
        warnings.warn(
            f"[yfinance] Option chain for {symbol} is RESEARCH DATA only. "
            "Greeks/IV are approximate. Do not use for execution.",
            stacklevel=2,
        )
        loop = asyncio.get_event_loop()
        chain = await loop.run_in_executor(
            None, self._fetch_option_chain, symbol, expiration
        )
        return chain

    def _fetch_option_chain(self, symbol: str, expiration: date) -> OptionChain:
        ticker = yf.Ticker(symbol)
        exp_str = expiration.strftime("%Y-%m-%d")

        available = ticker.options
        if exp_str not in available:
            closest = min(available, key=lambda d: abs(date.fromisoformat(d) - expiration))
            logger.warning(
                "yfinance: exact expiration %s not available for %s — using %s",
                exp_str,
                symbol,
                closest,
            )
            exp_str = closest
            expiration = date.fromisoformat(closest)

        raw = ticker.option_chain(exp_str)
        underlying_price = self._fetch_latest_price(symbol)

        chain = OptionChain(
            symbol=symbol,
            expiration=expiration,
            underlying_price=Decimal(str(round(underlying_price, 4))),
            fetched_at=datetime.utcnow(),
        )

        for side, df in (("call", raw.calls), ("put", raw.puts)):
            for _, row in df.iterrows():
                try:
                    contract = OptionContract(
                        symbol=symbol,
                        option_symbol=row.get("contractSymbol", ""),
                        expiration=expiration,
                        strike=Decimal(str(row["strike"])),
                        option_type=side,
                        bid=Decimal(str(row.get("bid", 0) or 0)),
                        ask=Decimal(str(row.get("ask", 0) or 0)),
                        last=Decimal(str(row.get("lastPrice", 0) or 0)),
                        volume=int(row.get("volume", 0) or 0),
                        open_interest=int(row.get("openInterest", 0) or 0),
                        implied_volatility=float(row.get("impliedVolatility", 0) or 0),
                        delta=None,  # yfinance doesn't provide Greeks reliably
                        gamma=None,
                        theta=None,
                        vega=None,
                    )
                    if side == "call":
                        chain.calls.append(contract)
                    else:
                        chain.puts.append(contract)
                except Exception as exc:
                    logger.debug("Skipping malformed contract row: %s", exc)

        logger.info(
            "yfinance option chain | %s %s | calls=%d puts=%d [RESEARCH ONLY]",
            symbol,
            expiration,
            len(chain.calls),
            len(chain.puts),
        )
        return chain

    async def get_available_expirations(self, symbol: str) -> List[date]:
        """Return list of available option expirations from Yahoo Finance."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._fetch_expirations, symbol)

    def _fetch_expirations(self, symbol: str) -> List[date]:
        ticker = yf.Ticker(symbol)
        return sorted(date.fromisoformat(d) for d in ticker.options)

    # ── Volatility helpers (research-grade) ──────────────────────────────────

    async def get_historical_volatility(
        self, symbol: str, window: int = 21, annualize: bool = True
    ) -> float:
        """
        Compute historical (realized) volatility.
        Returns annualized vol if annualize=True.
        """
        end = date.today()
        start = end - timedelta(days=window * 3)  # extra buffer for weekends
        df = await self.get_bars(symbol, start, end, "1d")
        if len(df) < window:
            raise ValueError(f"Insufficient data to compute {window}-day HV for {symbol}")
        log_ret = np.log(df["close"] / df["close"].shift(1)).dropna()
        hv = log_ret.tail(window).std()
        return float(hv * np.sqrt(252) if annualize else hv)

    # ── Black-Scholes utilities ───────────────────────────────────────────────

    @staticmethod
    def black_scholes_price(
        S: float,
        K: float,
        T: float,
        r: float,
        sigma: float,
        option_type: str = "call",
    ) -> float:
        """
        Black-Scholes price.  T in years.

        ⚠ APPROXIMATION: Used only when live broker IV is unavailable.
        """
        if T <= 0 or sigma <= 0:
            intrinsic = max(S - K, 0) if option_type == "call" else max(K - S, 0)
            return intrinsic
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        if option_type == "call":
            return float(S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2))
        return float(K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1))

    @staticmethod
    def bs_delta(
        S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call"
    ) -> float:
        """Black-Scholes delta — approximation only."""
        if T <= 0:
            if option_type == "call":
                return 1.0 if S > K else 0.0
            return -1.0 if S < K else 0.0
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        if option_type == "call":
            return float(norm.cdf(d1))
        return float(norm.cdf(d1) - 1)

    # ── VWAP ─────────────────────────────────────────────────────────────────

    @staticmethod
    def compute_vwap(df: pd.DataFrame) -> pd.Series:
        """
        Compute VWAP from intraday OHLCV DataFrame.
        Expects columns: open, high, low, close, volume.
        """
        typical = (df["high"] + df["low"] + df["close"]) / 3
        cum_tp_vol = (typical * df["volume"]).cumsum()
        cum_vol = df["volume"].cumsum()
        return cum_tp_vol / cum_vol
