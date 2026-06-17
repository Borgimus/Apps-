"""
ICT-aware data fetcher using yfinance.

Provides clean, UTC-indexed OHLCV DataFrames for 1-minute and daily bars.

DISCLAIMER: yfinance is an unofficial, research-grade data source.
All data is for backtesting / research only — do not use for live execution.
"""

from __future__ import annotations

import logging
import warnings
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

_RESEARCH_WARNING = (
    "[yfinance] Data is unofficial and for research/educational use only. "
    "Do not use for live order execution."
)

# yfinance limits 1-minute history to 30 days (7 days reliably).
_MAX_1MIN_DAYS = 29


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase columns, ensure UTC DatetimeIndex."""
    if df.empty:
        return df
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    # Keep only OHLCV columns
    desired = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[desired]
    if not hasattr(df.index, "tz") or df.index.tz is None:
        df.index = pd.to_datetime(df.index, utc=True)
    else:
        df.index = df.index.tz_convert("UTC")
    df = df.sort_index()
    return df


def fetch_1min_bars(
    symbol: str,
    start: str | date | None = None,
    end: str | date | None = None,
    *,
    warn: bool = True,
) -> pd.DataFrame:
    """
    Fetch 1-minute OHLCV bars for *symbol*.

    Parameters
    ----------
    symbol : str
        Ticker symbol (e.g. "ES=F", "SPY", "EURUSD=X").
    start : str | date | None
        Inclusive start date.  If None, uses 7 days ago.
        yfinance caps 1-minute history at ~30 days.
    end : str | date | None
        Exclusive end date.  If None, uses today.
    warn : bool
        Emit research disclaimer warning (default True).

    Returns
    -------
    pd.DataFrame
        Columns: open, high, low, close, volume
        Index: UTC-aware DatetimeIndex
    """
    if warn:
        warnings.warn(_RESEARCH_WARNING, stacklevel=2)

    today = date.today()
    if end is None:
        end_dt = today + timedelta(days=1)
    else:
        end_dt = pd.Timestamp(str(end)).date() + timedelta(days=1)

    if start is None:
        start_dt = today - timedelta(days=7)
    else:
        start_dt = pd.Timestamp(str(start)).date()

    # Guard: 1-minute data is limited
    max_start = today - timedelta(days=_MAX_1MIN_DAYS)
    if start_dt < max_start:
        logger.warning(
            "Requested 1-minute start %s is older than 30-day yfinance limit; "
            "truncating to %s",
            start_dt,
            max_start,
        )
        start_dt = max_start

    ticker = yf.Ticker(symbol)
    try:
        df = ticker.history(
            start=str(start_dt),
            end=str(end_dt),
            interval="1m",
            auto_adjust=True,
            progress=False,
        )
    except Exception as exc:
        logger.error("yfinance 1-minute fetch failed for %s: %s", symbol, exc)
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    if df.empty:
        logger.warning("yfinance returned empty 1-minute DataFrame for %s", symbol)
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = _normalise(df)
    logger.info("Fetched %d 1-minute bars for %s [%s → %s]", len(df), symbol, start_dt, end_dt)
    return df


def fetch_daily_bars(
    symbol: str,
    start: str | date | None = None,
    end: str | date | None = None,
    *,
    warn: bool = True,
) -> pd.DataFrame:
    """
    Fetch daily OHLCV bars for *symbol*.

    Parameters
    ----------
    symbol : str
    start : str | date | None
        If None, uses 365 days ago.
    end : str | date | None
        If None, uses today.
    warn : bool
        Emit research disclaimer warning.

    Returns
    -------
    pd.DataFrame
        Columns: open, high, low, close, volume
        Index: UTC-aware DatetimeIndex (daily)
    """
    if warn:
        warnings.warn(_RESEARCH_WARNING, stacklevel=2)

    today = date.today()
    if end is None:
        end_dt = today + timedelta(days=1)
    else:
        end_dt = pd.Timestamp(str(end)).date() + timedelta(days=1)

    if start is None:
        start_dt = today - timedelta(days=365)
    else:
        start_dt = pd.Timestamp(str(start)).date()

    ticker = yf.Ticker(symbol)
    try:
        df = ticker.history(
            start=str(start_dt),
            end=str(end_dt),
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
    except Exception as exc:
        logger.error("yfinance daily fetch failed for %s: %s", symbol, exc)
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    if df.empty:
        logger.warning("yfinance returned empty daily DataFrame for %s", symbol)
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = _normalise(df)
    logger.info("Fetched %d daily bars for %s [%s → %s]", len(df), symbol, start_dt, end_dt)
    return df


def fetch_bars(
    symbol: str,
    interval: str = "1m",
    start: str | date | None = None,
    end: str | date | None = None,
    *,
    warn: bool = True,
) -> pd.DataFrame:
    """
    Generic bar fetcher.  Routes to the appropriate specialist function.

    interval : one of "1m", "1d"  (others passed directly to yfinance).
    """
    if interval == "1m":
        return fetch_1min_bars(symbol, start, end, warn=warn)
    if interval == "1d":
        return fetch_daily_bars(symbol, start, end, warn=warn)

    # Generic fallback
    if warn:
        warnings.warn(_RESEARCH_WARNING, stacklevel=2)
    ticker = yf.Ticker(symbol)
    today = date.today()
    start_dt = str(start) if start else str(today - timedelta(days=365))
    end_dt = str(end) if end else str(today + timedelta(days=1))
    try:
        df = ticker.history(
            start=start_dt,
            end=end_dt,
            interval=interval,
            auto_adjust=True,
            progress=False,
        )
    except Exception as exc:
        logger.error("yfinance fetch failed for %s [%s]: %s", symbol, interval, exc)
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    return _normalise(df)
