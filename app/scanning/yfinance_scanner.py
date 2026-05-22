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
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")


@dataclass
class SymbolMetrics:
    symbol: str
    price: float
    atr: float
    atr_pct: float                  # ATR / price — how much the symbol moves daily
    rvol: float                      # relative volume vs 20d avg
    rsi: float                       # 14-period daily RSI
    vwap: float                      # intraday VWAP
    price_vs_vwap: str               # "above" | "below" | "at"
    opening_range_high: float        # high of first 30 min of today's session
    opening_range_low: float         # low  of first 30 min of today's session
    is_orb_breakout: bool            # price > opening_range_high
    is_orb_breakdown: bool           # price < opening_range_low
    trend: str                       # "up" | "down" | "sideways"
    ma_compression: bool             # 10/20/50 MAs within 1% of each other
    gap_pct: float                   # today open vs prior close (+ = gap up)
    volatility_5d: float             # 5-day realized vol (annualized)
    has_earnings_today: bool
    volume_today: int                # cumulative intraday volume
    avg_volume_20d: float            # 20-day average daily volume
    fetched_at: datetime = field(default_factory=lambda: datetime.now(tz=_ET))
    errors: List[str] = field(default_factory=list)
    universe_group: Optional[str] = None  # set by caller after UniverseLoader assigns group


class YFinanceScanner:
    """
    Computes SymbolMetrics for a list of symbols using yfinance.

    Usage:
        scanner = YFinanceScanner()
        results = await scanner.scan(["SPY", "QQQ", "AAPL"])
    """

    def __init__(self, orb_minutes: int = 30):
        self._orb_minutes = orb_minutes

    async def scan(self, symbols: List[str]) -> List[SymbolMetrics]:
        """Scan all symbols concurrently. Failures return partial metrics."""
        tasks = [self._scan_one(sym) for sym in symbols]
        return await asyncio.gather(*tasks, return_exceptions=False)

    async def scan_one(self, symbol: str) -> SymbolMetrics:
        return await self._scan_one(symbol)

    async def _scan_one(self, symbol: str) -> SymbolMetrics:
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._compute_metrics, symbol
            )
        except Exception as exc:
            logger.warning("YFinanceScanner: failed for %s: %s", symbol, exc)
            now = datetime.now(tz=_ET)
            return SymbolMetrics(
                symbol=symbol,
                price=0.0, atr=0.0, atr_pct=0.0, rvol=0.0, rsi=50.0,
                vwap=0.0, price_vs_vwap="unknown",
                opening_range_high=0.0, opening_range_low=0.0,
                is_orb_breakout=False, is_orb_breakdown=False,
                trend="unknown", ma_compression=False, gap_pct=0.0,
                volatility_5d=0.0, has_earnings_today=False,
                volume_today=0, avg_volume_20d=0.0,
                fetched_at=now, errors=[str(exc)],
            )

    def _compute_metrics(self, symbol: str) -> SymbolMetrics:
        import yfinance as yf

        now_et = datetime.now(tz=_ET)
        today = now_et.date()
        errors: List[str] = []

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            ticker = yf.Ticker(symbol)

            # ── Daily bars (60 days for ATR/RSI/MA/vol) ───────────────────────
            daily_start = today - timedelta(days=75)
            daily_df = ticker.history(
                start=str(daily_start), end=str(today + timedelta(days=1)),
                interval="1d", auto_adjust=True,
            )
            if daily_df.empty:
                raise ValueError(f"No daily data returned for {symbol}")
            daily_df.columns = [c.lower() for c in daily_df.columns]
            daily_df.index = pd.to_datetime(daily_df.index, utc=True)

            # ── Intraday bars (2-day window for VWAP / ORB) ───────────────────
            intra_df = ticker.history(
                period="2d", interval="5m", auto_adjust=True,
            )
            intra_df.columns = [c.lower() for c in intra_df.columns]
            intra_df.index = pd.to_datetime(intra_df.index, utc=True)

            # ── Earnings check ────────────────────────────────────────────────
            has_earnings = self._check_earnings(ticker, today)

        # ── Current price ─────────────────────────────────────────────────────
        price = float(daily_df["close"].iloc[-1])
        if price <= 0:
            raise ValueError(f"Invalid price {price} for {symbol}")

        # ── ATR (14-period, daily) ─────────────────────────────────────────────
        atr, atr_pct = self._compute_atr(daily_df, price)

        # ── RSI (14-period, daily) ─────────────────────────────────────────────
        rsi = self._compute_rsi(daily_df)

        # ── RVOL (today / 20d avg daily volume) ───────────────────────────────
        rvol, volume_today, avg_vol_20d = self._compute_rvol(daily_df, intra_df, today)

        # ── Trend (price vs 20/50 SMA) ────────────────────────────────────────
        trend, ma_compression = self._compute_trend(daily_df, price)

        # ── Gap % (today open vs prior close) ─────────────────────────────────
        gap_pct = self._compute_gap(daily_df)

        # ── Realized volatility 5d ────────────────────────────────────────────
        vol_5d = self._compute_vol5d(daily_df)

        # ── VWAP and opening range (intraday) ─────────────────────────────────
        vwap, price_vs_vwap, orb_high, orb_low, is_orb_breakout, is_orb_breakdown = (
            self._compute_intraday(intra_df, price, today, now_et)
        )

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
        )

    # ── Sub-computations ──────────────────────────────────────────────────────

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
        # Today's cumulative intraday volume
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

        # MA compression: all three MAs within 1% of each other
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
        price: float,
        today: date,
        now_et: datetime,
    ):
        today_mask = intra_df.index.date == today
        today_bars = intra_df.loc[today_mask]

        if today_bars.empty:
            return price, "unknown", price, price, False, False

        # VWAP
        typical = (today_bars["high"] + today_bars["low"] + today_bars["close"]) / 3
        cum_vol = today_bars["volume"].cumsum()
        vwap_series = (typical * today_bars["volume"]).cumsum() / cum_vol.replace(0, np.nan)
        vwap = float(vwap_series.iloc[-1]) if not vwap_series.empty else price
        if np.isnan(vwap):
            vwap = price

        tol = vwap * 0.001
        if price > vwap + tol:
            price_vs_vwap = "above"
        elif price < vwap - tol:
            price_vs_vwap = "below"
        else:
            price_vs_vwap = "at"

        # Opening range: first orb_minutes of the 09:30 ET session (vectorized)
        et_idx = today_bars.index.tz_convert(_ET)
        minutes_from_open = et_idx.hour * 60 + et_idx.minute
        orb_mask = (minutes_from_open >= 9 * 60 + 30) & (minutes_from_open < 9 * 60 + 30 + self._orb_minutes)
        orb_bars_et = today_bars[orb_mask]

        if not orb_bars_et.empty:
            orb_high = float(orb_bars_et["high"].max())
            orb_low  = float(orb_bars_et["low"].min())
        else:
            orb_high = price
            orb_low  = price

        is_orb_breakout  = price > orb_high
        is_orb_breakdown = price < orb_low

        return vwap, price_vs_vwap, orb_high, orb_low, is_orb_breakout, is_orb_breakdown

    @staticmethod
    def _check_earnings(ticker, today: date) -> bool:
        try:
            cal = ticker.calendar
            if cal is None:
                return False
            # Different yfinance versions return different types
            if isinstance(cal, dict):
                earnings_dates = cal.get("Earnings Date", [])
                if earnings_dates:
                    # May be a list or a single value
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
                # DataFrame format
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
