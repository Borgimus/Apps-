"""
Signal quality scoring for paper evaluation diagnostics.

Scores are integer 0-4.  Higher = higher quality setup.

These scores are used for deterministic signal ranking and bridge diagnostics
when PAPER_EVAL_PERMISSIVE_ENTRY_MODE is enabled.  They are ADVISORY ONLY —
they do not alter strategy gate logic, thresholds, or position sizing.

VWAP quality (0-4):
  1. Prior trend clearly on opposite side of VWAP (3+ of last 5 bars)
  2. Reclaim/rejection candle closes cleanly through VWAP (>0.1% clear)
  3. Volume above rolling mean of prior 10 bars
  4. Next bar confirms direction (stays above/below VWAP)

ORB quality (0-4):
  1. Breakout candle closes outside range (>0.1% clear)
  2. Breakout bar volume above session average
  3. Range width reasonable: 0.3%–2.5% of underlying price
  4. Breakout direction aligns with intraday VWAP

RSI_trend:
  Readiness logged separately; no quality score (return 0).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from .strategy_base import Signal

logger = logging.getLogger(__name__)


def score_vwap_signal(signal: "Signal", bars: pd.DataFrame) -> int:
    """VWAP reclaim/rejection quality, 0–4."""
    score = 0
    try:
        from .strategy_base import SignalDirection
        from zoneinfo import ZoneInfo

        bars = bars.copy()
        bars.columns = bars.columns.str.lower()

        sig_ts = signal.timestamp
        if hasattr(sig_ts, "astimezone"):
            sig_ts_utc = sig_ts.astimezone(ZoneInfo("UTC"))
        else:
            sig_ts_utc = sig_ts

        ts_diff = abs((bars.index - sig_ts_utc).total_seconds())
        if ts_diff.empty:
            return 0
        sig_idx = int(ts_diff.argmin())
        if sig_idx < 3:
            return 0

        # Per-day VWAP
        bars["_date"] = bars.index.date
        vwap_vals: pd.Series = pd.Series(index=bars.index, dtype=float)
        for _, g in bars.groupby("_date"):
            typical = (g["high"] + g["low"] + g["close"]) / 3
            cum_tp = (typical * g["volume"]).cumsum()
            cum_v = g["volume"].cumsum().replace(0, float("nan"))
            vwap_vals[g.index] = cum_tp / cum_v
        bars["_vwap"] = vwap_vals

        close = bars["close"].values
        vwap = bars["_vwap"].values
        vol = bars["volume"].values
        is_long = signal.direction == SignalDirection.LONG

        # 1. Prior trend on opposite side of VWAP
        prior = range(max(0, sig_idx - 5), sig_idx)
        if len(prior) >= 3:
            if is_long:
                below = sum(1 for i in prior if close[i] < vwap[i])
                score += 1 if below >= 3 else 0
            else:
                above = sum(1 for i in prior if close[i] > vwap[i])
                score += 1 if above >= 3 else 0

        # 2. Candle closes cleanly through VWAP (>0.1%)
        v_now = vwap[sig_idx]
        c_now = close[sig_idx]
        if v_now and v_now > 0:
            clearance = abs(c_now - v_now) / v_now
            if is_long and c_now > v_now and clearance > 0.001:
                score += 1
            elif not is_long and c_now < v_now and clearance > 0.001:
                score += 1

        # 3. Volume above prior-10-bar mean
        prior_vol = vol[max(0, sig_idx - 10):sig_idx]
        if len(prior_vol) > 0:
            avg_vol = prior_vol.mean()
            if avg_vol > 0 and vol[sig_idx] > avg_vol:
                score += 1

        # 4. Next bar confirms direction
        if sig_idx + 1 < len(close) and not pd.isna(vwap[sig_idx + 1]):
            nxt_c = close[sig_idx + 1]
            nxt_v = vwap[sig_idx + 1]
            if (is_long and nxt_c > nxt_v) or (not is_long and nxt_c < nxt_v):
                score += 1

    except Exception as exc:
        logger.debug("VWAP quality score error for %s: %s", signal.symbol, exc)

    return min(4, score)


def score_orb_signal(signal: "Signal", bars: pd.DataFrame) -> int:
    """ORB breakout quality, 0–4."""
    score = 0
    try:
        from .strategy_base import SignalDirection
        from zoneinfo import ZoneInfo

        bars = bars.copy()
        bars.columns = bars.columns.str.lower()
        et = ZoneInfo("America/New_York")

        sig_ts = signal.timestamp
        if hasattr(sig_ts, "astimezone"):
            sig_ts_utc = sig_ts.astimezone(ZoneInfo("UTC"))
        else:
            sig_ts_utc = sig_ts

        ts_diff = abs((bars.index - sig_ts_utc).total_seconds())
        if ts_diff.empty:
            return 0
        sig_idx = int(ts_diff.argmin())

        bars_et = bars.copy()
        bars_et.index = bars.index.tz_convert(et)
        sig_date = bars_et.index[sig_idx].date()
        today_et = bars_et[bars_et.index.date == sig_date]

        if today_et.empty:
            return 0

        # Opening range = first 15 bars
        orb = today_et.head(15)
        or_high = float(orb["high"].max())
        or_low = float(orb["low"].min())
        or_range = or_high - or_low

        # Locate the signal bar within today_et
        try:
            local_pos = list(today_et.index).index(bars_et.index[sig_idx])
        except ValueError:
            local_pos = -1

        sig_close = float(today_et["close"].iloc[local_pos]) if local_pos >= 0 else signal.price
        price = sig_close
        is_long = signal.direction == SignalDirection.LONG

        # 1. Close clearly outside range (>0.1% beyond range boundary)
        if is_long and or_high > 0:
            if sig_close > or_high and (sig_close - or_high) / or_high > 0.001:
                score += 1
        elif not is_long and or_low > 0:
            if sig_close < or_low and (or_low - sig_close) / or_low > 0.001:
                score += 1

        # 2. Breakout volume above session average
        if local_pos > 0:
            session_vol = today_et["volume"].values
            avg_vol = session_vol[:local_pos].mean()
            if avg_vol > 0 and session_vol[local_pos] > avg_vol:
                score += 1

        # 3. Range width reasonable: 0.3%–2.5% of price
        if price > 0 and or_range > 0:
            range_pct = or_range / price
            if 0.003 <= range_pct <= 0.025:
                score += 1

        # 4. Breakout aligns with VWAP direction
        typical = (today_et["high"] + today_et["low"] + today_et["close"]) / 3
        cum_v = today_et["volume"].cumsum().replace(0, float("nan"))
        vwap_s = (typical * today_et["volume"]).cumsum() / cum_v
        if not vwap_s.empty and local_pos >= 0:
            vwap_now = float(vwap_s.iloc[min(local_pos, len(vwap_s) - 1)])
            if (is_long and price > vwap_now) or (not is_long and price < vwap_now):
                score += 1

    except Exception as exc:
        logger.debug("ORB quality score error for %s: %s", signal.symbol, exc)

    return min(4, score)


def compute_signal_quality_score(signal: "Signal", bars: pd.DataFrame) -> float:
    """
    Route to the appropriate quality scorer by strategy_id.
    Returns a float 0–4.

    RSI_trend returns 0 — readiness is logged separately and is not a
    quality score (not-ready is not a failure; it's a timing constraint).
    """
    sid = signal.strategy_id
    if sid == "vwap_reclaim":
        return float(score_vwap_signal(signal, bars))
    if sid == "orb":
        return float(score_orb_signal(signal, bars))
    # rsi_trend and others: no quality score in this framework
    return 0.0
