"""
YFinance Market Scanner — research-grade intraday metrics.

IMPORTANT: yfinance data is RESEARCH ONLY.
Do not use these metrics for execution decisions.
All execution quotes must come from Alpaca.

For each symbol, computes:
  - ATR (14-period), ATR%
  - Relative volume (RVOL = today_volume / 20d_avg_volume)
  - RSI (14-period, daily)
  - VWAP and price-vs-VWAP relationship
  - Opening range (first 30 minutes: high/low, breakout flag)
  - Trend (price vs 20/50 SMA)
  - MA compression (10/20/50 within 1% of each other)
  - Gap% (today open vs prior close)
  - 5-day realized volatility
  - Earnings flag (has earnings today)

Price source:
  - During market hours, `price` is the latest available intraday 5m bar close.
  - `previous_close` holds the prior daily session close.
  - If no intraday bars exist for today, falls back to previous_close and sets
    price_source="daily_close_fallback" plus is_data_stale=True.

Freshness tracking:
  - latest_intraday_bar_timestamp: ET datetime of last 5m bar received
  - intraday_data_age_seconds: seconds between that bar and fetch time
  - is_data_stale: True when intraday_data_age_seconds > max_intraday_data_age_seconds

Data fetch:
  - yf.download() batches all symbols in two parallel threaded calls (daily + intraday)
    instead of sequential per-symbol Ticker.history() calls.
  - YF_EMAIL / YF_PASSWORD env vars enable authenticated Yahoo Finance requests,
    which reduces the risk of the "possibly delisted; no price data found" false-positive
    that affects unauthenticated sessions under rate limiting.
  - yf.config.debug.hide_exceptions = False: data failures raise exceptions rather than
    silently returning empty DataFrames.
"""

from __future__ import annotations

import asyncio
import logging
import os
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests as _requests
import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")


def _make_session() -> _requests.Session:
    """
    Return a requests.Session that trusts the container proxy CA bundle.

    yfinance v1.4.x defaults to curl_cffi as its HTTP backend, which does not
    read REQUESTS_CA_BUNDLE / SSL_CERT_FILE and therefore fails TLS in
    proxy-terminated environments. Passing an explicit requests.Session forces
    the requests backend, which already picks up those env vars.
    """
    sess = _requests.Session()
    ca = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")
    if ca and os.path.exists(ca):
        sess.verify = ca
    return sess


def _configure_yfinance() -> None:
    """Apply global yfinance settings once at import time."""
    # Raise exceptions on data failures rather than silently returning empty DataFrames.
    yf.config.debug.hide_exceptions = False

    email = os.getenv("YF_EMAIL", "").strip()
    password = os.getenv("YF_PASSWORD", "").strip()
    if email and password:
        try:
            yf.Auth(email, password)
            logger.info(
                "YFinanceScanner: authenticated with Yahoo Finance account %s", email
            )
        except Exception as exc:
            logger.warning(
                "YFinanceScanner: Yahoo Finance auth failed — proceeding unauthenticated: %s",
                exc,
            )
    else:
        logger.debug(
            "YFinanceScanner: YF_EMAIL/YF_PASSWORD not set — unauthenticated requests"
        )


_configure_yfinance()


@dataclass
class SymbolMetrics:
    symbol: str
    price: float                         # latest intraday close (or previous_close fallback)
    atr: float
    atr_pct: float                       # ATR / price
    rvol: float                          # relative volume vs 20d avg
    rsi: float                           # 14-period daily RSI
    vwap: float                          # intraday VWAP from today's bars
    price_vs_vwap: str                   # "above" | "below" | "at" | "unknown"
    opening_range_high: float            # high of first 30 min of today's session
    opening_range_low: float             # low  of first 30 min of today's session
    is_orb_breakout: bool                # latest intraday close > opening_range_high
    is_orb_breakdown: bool               # latest intraday close < opening_range_low
    trend: str                           # "up" | "down" | "sideways" (intraday close vs daily MAs)
    ma_compression: bool                 # 10/20/50 MAs within 1% of each other
    gap_pct: float                       # today open vs prior close (+ = gap up)
    volatility_5d: float                 # 5-day realized vol (annualized)
    has_earnings_today: bool
    volume_today: int                    # cumulative intraday volume
    avg_volume_20d: float                # 20-day average daily volume
    fetched_at: datetime = field(default_factory=lambda: datetime.now(tz=_ET))
    errors: List[str] = field(default_factory=list)
    universe_group: Optional[str] = None
    # Price source and freshness fields
    previous_close: float = 0.0                              # prior daily session close
    price_source: str = "daily_close_fallback"               # "intraday_close" | "daily_close_fallback"
    latest_intraday_bar_timestamp: Optional[datetime] = None # ET datetime of last 5m bar
    intraday_data_age_seconds: float = 0.0                   # seconds since latest intraday bar
    daily_bar_timestamp: Optional[datetime] = None           # ET datetime of last daily bar
    daily_data_age_seconds: float = 0.0                      # seconds since last daily bar
    is_data_stale: bool = False                              # True when intraday data exceeds max age


class YFinanceScanner:
    """
    Computes SymbolMetrics for a list of symbols using yfinance.

    Data is fetched via two batched yf.download() calls (daily + intraday),
    each of which downloads all symbols in parallel using threads. This replaces
    the prior sequential per-symbol Ticker.history() approach and eliminates the
    ~3-minute pre-session scan delay.

    Usage:
        scanner = YFinanceScanner()
        results = await scanner.scan(["SPY", "QQQ", "AAPL"])

    Args:
        orb_minutes: Length of the opening range window in minutes (default 30).
        max_intraday_data_age_seconds: Intraday bar age beyond which a symbol is
            marked is_data_stale=True and rejected by CandidateScorer (default 1200 = 20 min).

    Environment:
        YF_EMAIL / YF_PASSWORD: Optional Yahoo Finance account credentials for
            authenticated API access. Reduces false-positive "possibly delisted"
            errors under unauthenticated rate limiting.
    """

    def __init__(self, orb_minutes: int = 30, max_intraday_data_age_seconds: int = 1200):
        self._orb_minutes = orb_minutes
        self._max_intraday_data_age_seconds = max_intraday_data_age_seconds

    async def scan(self, symbols: List[str]) -> List[SymbolMetrics]:
        """
        Scan all symbols. Fetches all data in two batched calls, then computes
        metrics per symbol concurrently. Failures return sentinel SymbolMetrics.
        """
        now_et = datetime.now(tz=_ET)
        today = now_et.date()
        daily_cache, intra_cache = await asyncio.get_event_loop().run_in_executor(
            None, self._batch_fetch, symbols, today
        )
        tasks = [
            self._scan_one(
                sym,
                daily_cache.get(sym),
                intra_cache.get(sym, pd.DataFrame()),
                now_et,
                today,
            )
            for sym in symbols
        ]
        return await asyncio.gather(*tasks, return_exceptions=False)

    async def scan_one(self, symbol: str) -> SymbolMetrics:
        now_et = datetime.now(tz=_ET)
        today = now_et.date()
        daily_cache, intra_cache = await asyncio.get_event_loop().run_in_executor(
            None, self._batch_fetch, [symbol], today
        )
        return await self._scan_one(
            symbol,
            daily_cache.get(symbol),
            intra_cache.get(symbol, pd.DataFrame()),
            now_et,
            today,
        )

    async def _scan_one(
        self,
        symbol: str,
        daily_df: Optional[pd.DataFrame],
        intra_df: pd.DataFrame,
        now_et: datetime,
        today: date,
    ) -> SymbolMetrics:
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._compute_metrics, symbol, daily_df, intra_df, now_et, today
            )
        except Exception as exc:
            logger.warning("YFinanceScanner: failed for %s: %s", symbol, exc)
            return SymbolMetrics(
                symbol=symbol,
                price=0.0, atr=0.0, atr_pct=0.0, rvol=0.0, rsi=50.0,
                vwap=0.0, price_vs_vwap="unknown",
                opening_range_high=0.0, opening_range_low=0.0,
                is_orb_breakout=False, is_orb_breakdown=False,
                trend="unknown", ma_compression=False, gap_pct=0.0,
                volatility_5d=0.0, has_earnings_today=False,
                volume_today=0, avg_volume_20d=0.0,
                fetched_at=now_et, errors=[str(exc)],
                is_data_stale=True,
            )

    # ── Batch data fetch ──────────────────────────────────────────────────────

    def _batch_fetch(
        self, symbols: List[str], today: date
    ) -> Tuple[Dict[str, Optional[pd.DataFrame]], Dict[str, pd.DataFrame]]:
        """
        Download daily and intraday bars for all symbols in two parallel calls.

        Returns:
            daily_cache:  symbol → DataFrame (None if data unavailable)
            intra_cache:  symbol → DataFrame (empty DataFrame if unavailable)
        """
        daily_start = today - timedelta(days=75)
        end_date = str(today + timedelta(days=1))
        tickers_str = " ".join(symbols)

        sess = _make_session()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            try:
                daily_all = yf.download(
                    tickers=tickers_str,
                    start=str(daily_start),
                    end=end_date,
                    interval="1d",
                    auto_adjust=True,
                    threads=True,
                    progress=False,
                    group_by="ticker",
                    session=sess,
                )
            except Exception as exc:
                logger.error("YFinanceScanner: batch daily download failed: %s", exc)
                daily_all = pd.DataFrame()

            try:
                intra_all = yf.download(
                    tickers=tickers_str,
                    period="2d",
                    interval="5m",
                    auto_adjust=True,
                    threads=True,
                    progress=False,
                    group_by="ticker",
                    session=sess,
                )
            except Exception as exc:
                logger.error("YFinanceScanner: batch intraday download failed: %s", exc)
                intra_all = pd.DataFrame()

        daily_cache: Dict[str, Optional[pd.DataFrame]] = {}
        intra_cache: Dict[str, pd.DataFrame] = {}

        for sym in symbols:
            daily_cache[sym] = self._extract_symbol(daily_all, sym, dropna_col="close")
            intra_cache[sym] = (
                self._extract_symbol(intra_all, sym, dropna_col=None) or pd.DataFrame()
            )

        failed_daily = [s for s, df in daily_cache.items() if df is None]
        if failed_daily:
            logger.warning(
                "YFinanceScanner: batch daily fetch returned no data for %d/%d symbols: %s",
                len(failed_daily), len(symbols), failed_daily,
            )

        return daily_cache, intra_cache

    @staticmethod
    def _extract_symbol(
        download_df: pd.DataFrame,
        symbol: str,
        dropna_col: Optional[str],
    ) -> Optional[pd.DataFrame]:
        """
        Slice a per-symbol DataFrame from a yf.download() result.

        yf.download(group_by="ticker") returns a MultiIndex DataFrame when multiple
        tickers are requested. For a single ticker it may return plain columns.
        Returns None when the symbol has no data in the result.
        """
        if download_df.empty:
            return None
        try:
            if isinstance(download_df.columns, pd.MultiIndex):
                top_level = download_df.columns.get_level_values(0)
                if symbol not in top_level:
                    return None
                df = download_df[symbol].copy()
            else:
                df = download_df.copy()

            df.columns = [c.lower() for c in df.columns]
            df.index = pd.to_datetime(df.index, utc=True)

            if dropna_col and dropna_col in df.columns:
                df = df.dropna(subset=[dropna_col])

            return df if not df.empty else None
        except Exception:
            return None

    # ── Per-symbol metric computation ─────────────────────────────────────────

    def _compute_metrics(
        self,
        symbol: str,
        daily_df: Optional[pd.DataFrame],
        intra_df: pd.DataFrame,
        now_et: datetime,
        today: date,
    ) -> SymbolMetrics:
        errors: List[str] = []

        if daily_df is None or daily_df.empty:
            raise ValueError(f"No daily data returned for {symbol}")

        # ── Earnings check (per-symbol calendar call — not part of history batch) ──
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            has_earnings = self._check_earnings(
                yf.Ticker(symbol, session=_make_session()), today
            )

        # ── Previous close from daily bars ────────────────────────────────────
        previous_close = float(daily_df["close"].iloc[-1])
        if previous_close <= 0:
            raise ValueError(f"Invalid previous_close {previous_close} for {symbol}")

        # ── Daily bar timestamp and age ────────────────────────────────────────
        daily_bar_ts_et = _ts_to_et(daily_df.index[-1])
        daily_data_age = max(0.0, (now_et - daily_bar_ts_et).total_seconds())

        # ── Intraday metrics: price, VWAP, ORB ────────────────────────────────
        (
            intraday_price, latest_bar_ts_et,
            vwap, price_vs_vwap,
            orb_high, orb_low,
            is_orb_breakout, is_orb_breakdown,
        ) = self._compute_intraday(intra_df, today, now_et)

        # ── Current price selection ────────────────────────────────────────────
        if intraday_price is not None:
            price = intraday_price
            price_source = "intraday_close"
        else:
            price = previous_close
            price_source = "daily_close_fallback"
            errors.append("no_intraday_bars_today")
            logger.warning(
                "YFinanceScanner: no intraday bars for %s today — "
                "falling back to previous_close=%.2f",
                symbol, previous_close,
            )

        if vwap is None:
            vwap = price
            price_vs_vwap = "unknown"
        if orb_high is None:
            orb_high = price
            orb_low = price

        # ── Intraday data age and staleness ───────────────────────────────────
        if latest_bar_ts_et is not None:
            intraday_data_age = max(0.0, (now_et - latest_bar_ts_et).total_seconds())
            is_data_stale = intraday_data_age > self._max_intraday_data_age_seconds
        else:
            intraday_data_age = float("inf")
            is_data_stale = True

        if is_data_stale:
            age_desc = (
                f"{intraday_data_age:.0f}s"
                if intraday_data_age != float("inf")
                else "no bars"
            )
            logger.warning(
                "YFinanceScanner: %s intraday data stale (age=%s, threshold=%ds)",
                symbol, age_desc, self._max_intraday_data_age_seconds,
            )

        if price <= 0:
            raise ValueError(f"Invalid price {price} for {symbol}")

        # ── ATR (14-period, daily) ─────────────────────────────────────────────
        atr, atr_pct = self._compute_atr(daily_df, price)

        # ── RSI (14-period, daily) ─────────────────────────────────────────────
        rsi = self._compute_rsi(daily_df)

        # ── RVOL (today / 20d avg daily volume) ───────────────────────────────
        rvol, volume_today, avg_vol_20d = self._compute_rvol(daily_df, intra_df, today)

        # ── Trend: intraday close vs daily MAs (mixed timeframe) ──────────────
        trend, ma_compression = self._compute_trend(daily_df, price)

        # ── Gap % (today open vs prior close) ─────────────────────────────────
        gap_pct = self._compute_gap(daily_df)

        # ── Realized volatility 5d ────────────────────────────────────────────
        vol_5d = self._compute_vol5d(daily_df)

        return SymbolMetrics(
            symbol=symbol,
            price=price,
            atr=atr,
            atr_pct=atr_pct,
            rvol=rvol,
            rsi=rsi,
            vwap=vwap,
            price_vs_vwap=price_vs_vwap,
            opening_range_high=orb_high,
            opening_range_low=orb_low,
            is_orb_breakout=is_orb_breakout,
            is_orb_breakdown=is_orb_breakdown,
            trend=trend,
            ma_compression=ma_compression,
            gap_pct=gap_pct,
            volatility_5d=vol_5d,
            has_earnings_today=has_earnings,
            volume_today=volume_today,
            avg_volume_20d=avg_vol_20d,
            fetched_at=now_et,
            errors=errors,
            previous_close=previous_close,
            price_source=price_source,
            latest_intraday_bar_timestamp=latest_bar_ts_et,
            intraday_data_age_seconds=intraday_data_age,
            daily_bar_timestamp=daily_bar_ts_et,
            daily_data_age_seconds=daily_data_age,
            is_data_stale=is_data_stale,
        )

    # ── Sub-computations (unchanged) ──────────────────────────────────────────

    @staticmethod
    def _compute_atr(df: pd.DataFrame, price: float):
        if len(df) < 2:
            return 0.0, 0.0
        hl = df["high"] - df["low"]
        hc = (df["high"] - df["close"].shift(1)).abs()
        lc = (df["low"]  - df["close"].shift(1)).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        atr = float(tr.ewm(span=14, adjust=False).mean().iloc[-1])
        atr_pct = atr / price if price > 0 else 0.0
        return round(atr, 4), round(atr_pct, 6)

    @staticmethod
    def _compute_rsi(df: pd.DataFrame) -> float:
        if len(df) < 15:
            return 50.0
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(span=14, adjust=False).mean()
        avg_loss = loss.ewm(span=14, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = (100 - 100 / (1 + rs)).iloc[-1]
        return round(float(rsi) if not np.isnan(rsi) else 50.0, 2)

    @staticmethod
    def _compute_rvol(daily_df: pd.DataFrame, intra_df: pd.DataFrame, today: date):
        avg_vol_20d = float(daily_df["volume"].tail(20).mean()) if len(daily_df) >= 20 else 0.0
        today_mask = intra_df.index.date == today
        volume_today = int(intra_df.loc[today_mask, "volume"].sum()) if today_mask.any() else 0
        rvol = volume_today / avg_vol_20d if avg_vol_20d > 0 else 0.0
        return round(rvol, 3), volume_today, round(avg_vol_20d, 0)

    @staticmethod
    def _compute_trend(df: pd.DataFrame, price: float):
        close = df["close"]
        ma10 = float(close.rolling(10).mean().iloc[-1]) if len(df) >= 10 else price
        ma20 = float(close.rolling(20).mean().iloc[-1]) if len(df) >= 20 else price
        ma50 = float(close.rolling(50).mean().iloc[-1]) if len(df) >= 50 else price

        if price > ma20 > ma50:
            trend = "up"
        elif price < ma20 < ma50:
            trend = "down"
        else:
            trend = "sideways"

        if ma20 > 0 and ma50 > 0 and ma10 > 0:
            spread = max(ma10, ma20, ma50) - min(ma10, ma20, ma50)
            ma_compression = (spread / ma20) < 0.01
        else:
            ma_compression = False

        return trend, ma_compression

    @staticmethod
    def _compute_gap(df: pd.DataFrame) -> float:
        if len(df) < 2:
            return 0.0
        prev_close = float(df["close"].iloc[-2])
        today_open = float(df["open"].iloc[-1])
        if prev_close <= 0:
            return 0.0
        return round((today_open - prev_close) / prev_close, 6)

    @staticmethod
    def _compute_vol5d(df: pd.DataFrame) -> float:
        if len(df) < 6:
            return 0.0
        log_ret = np.log(df["close"] / df["close"].shift(1)).dropna()
        vol = float(log_ret.tail(5).std() * np.sqrt(252))
        return round(vol, 6)

    def _compute_intraday(
        self,
        intra_df: pd.DataFrame,
        today: date,
        now_et: datetime,
    ):
        """
        Derive all intraday metrics from today's 5-minute bars.

        Returns:
            (latest_bar_close, latest_bar_ts_et, vwap, price_vs_vwap,
             orb_high, orb_low, is_orb_breakout, is_orb_breakdown)

        latest_bar_close and latest_bar_ts_et are None when no bars exist for today.
        All ORB and VWAP comparisons use latest_bar_close — never an external price.
        """
        today_mask = intra_df.index.date == today
        today_bars = intra_df.loc[today_mask]

        if today_bars.empty:
            return None, None, None, "unknown", None, None, False, False

        latest_bar_close = float(today_bars["close"].iloc[-1])
        latest_bar_ts_et = _ts_to_et(today_bars.index[-1])

        typical = (today_bars["high"] + today_bars["low"] + today_bars["close"]) / 3
        cum_vol = today_bars["volume"].cumsum()
        vwap_series = (typical * today_bars["volume"]).cumsum() / cum_vol.replace(0, np.nan)
        vwap = float(vwap_series.iloc[-1]) if not vwap_series.empty else latest_bar_close
        if np.isnan(vwap):
            vwap = latest_bar_close

        tol = vwap * 0.001
        if latest_bar_close > vwap + tol:
            price_vs_vwap = "above"
        elif latest_bar_close < vwap - tol:
            price_vs_vwap = "below"
        else:
            price_vs_vwap = "at"

        et_idx = today_bars.index.tz_convert(_ET)
        minutes_from_open = et_idx.hour * 60 + et_idx.minute
        orb_mask = (
            (minutes_from_open >= 9 * 60 + 30) &
            (minutes_from_open < 9 * 60 + 30 + self._orb_minutes)
        )
        orb_bars = today_bars[orb_mask]

        if not orb_bars.empty:
            orb_high = float(orb_bars["high"].max())
            orb_low  = float(orb_bars["low"].min())
        else:
            orb_high = latest_bar_close
            orb_low  = latest_bar_close

        is_orb_breakout  = latest_bar_close > orb_high
        is_orb_breakdown = latest_bar_close < orb_low

        return (
            latest_bar_close, latest_bar_ts_et,
            vwap, price_vs_vwap,
            orb_high, orb_low,
            is_orb_breakout, is_orb_breakdown,
        )

    @staticmethod
    def _check_earnings(ticker, today: date) -> bool:
        try:
            cal = ticker.calendar
            if cal is None:
                return False
            if isinstance(cal, dict):
                earnings_dates = cal.get("Earnings Date", [])
                if earnings_dates:
                    if not hasattr(earnings_dates, "__iter__"):
                        earnings_dates = [earnings_dates]
                    for ed in earnings_dates:
                        try:
                            if hasattr(ed, "date"):
                                ed = ed.date()
                            elif isinstance(ed, str):
                                ed = date.fromisoformat(ed[:10])
                            if ed == today:
                                return True
                        except Exception:
                            pass
            elif hasattr(cal, "columns"):
                for col in ("Earnings Date", "Earnings High", "Earnings Low"):
                    if col in cal.columns:
                        for val in cal[col].dropna():
                            try:
                                if hasattr(val, "date"):
                                    val = val.date()
                                if val == today:
                                    return True
                            except Exception:
                                pass
        except Exception:
            pass
        return False


def _ts_to_et(ts) -> datetime:
    """Convert a pandas Timestamp (UTC-aware or naive) to an ET-aware datetime."""
    dt = pd.Timestamp(ts).to_pydatetime()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_ET)
