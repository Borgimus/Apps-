"""
ORB forward performance — post-session computation.

For every ORB signal in signal_bridge (traded or not), fetches 5-min
intraday bars for the underlying and computes the hypothetical price and
percent return at +5, +15, and +30 minutes from signal time.

Called by post_session.run_post_session() after the session ends.
Direction-aware: uses signal_direction to determine sign of return
(LONG: positive means price rose; SHORT: positive means price fell).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")

_FWD_OFFSETS_MIN = [5, 15, 30]


async def compute_orb_forward_performance(
    db_session,
    session_date: str,
    broker=None,
) -> int:
    """
    Fill orb_fwd_* columns for all ORB rows in signal_bridge for session_date.

    Returns the number of rows updated.
    """
    try:
        from sqlalchemy import select, update
        from app.api.models import DBSignalBridge
    except ImportError as exc:
        logger.warning("ORB forward perf: import error — %s", exc)
        return 0

    try:
        orb_rows = (
            await db_session.execute(
                select(DBSignalBridge).where(
                    DBSignalBridge.session_date == session_date,
                    DBSignalBridge.strategy_id == "orb",
                )
            )
        ).scalars().all()
    except Exception as exc:
        logger.warning("ORB forward perf: DB query failed — %s", exc)
        return 0

    if not orb_rows:
        return 0

    # Fetch 5-min bars per unique symbol (one fetch covers all signals for that symbol)
    symbol_bars: dict = {}
    for row in orb_rows:
        sym = row.symbol
        if sym in symbol_bars:
            continue
        try:
            bars = await _fetch_bars(sym, session_date, broker)
            symbol_bars[sym] = bars
        except Exception as exc:
            logger.warning("ORB forward perf: bars fetch failed for %s — %s", sym, exc)
            symbol_bars[sym] = None

    updated = 0
    for row in orb_rows:
        bars = symbol_bars.get(row.symbol)
        if bars is None or not bars:
            continue

        signal_ts = _to_utc(row.timestamp)
        entry_price = row.underlying_price_at_signal
        if entry_price is None or entry_price <= 0:
            continue

        direction = (row.signal_direction or "long").lower()
        sign = 1.0 if direction == "long" else -1.0

        fwd: dict = {}
        for offset_min in _FWD_OFFSETS_MIN:
            target_ts = signal_ts + timedelta(minutes=offset_min)
            price = _lookup_bar_close(bars, target_ts)
            if price is not None:
                pct = sign * (price - entry_price) / entry_price
                fwd[offset_min] = (price, pct)

        if not fwd:
            continue

        try:
            await db_session.execute(
                update(DBSignalBridge)
                .where(DBSignalBridge.id == row.id)
                .values(
                    orb_fwd_price_5m=fwd.get(5, (None, None))[0],
                    orb_fwd_price_15m=fwd.get(15, (None, None))[0],
                    orb_fwd_price_30m=fwd.get(30, (None, None))[0],
                    orb_fwd_pct_5m=fwd.get(5, (None, None))[1],
                    orb_fwd_pct_15m=fwd.get(15, (None, None))[1],
                    orb_fwd_pct_30m=fwd.get(30, (None, None))[1],
                )
            )
            updated += 1
        except Exception as exc:
            logger.warning("ORB forward perf: update failed for id=%s — %s", row.id, exc)

    if updated:
        try:
            await db_session.commit()
        except Exception as exc:
            logger.warning("ORB forward perf: commit failed — %s", exc)

    logger.info("ORB forward performance: updated %d/%d rows for %s", updated, len(orb_rows), session_date)
    return updated


async def _fetch_bars(symbol: str, session_date: str, broker=None) -> list:
    """Return list of (timestamp_utc, close) tuples for the session date."""
    try:
        import yfinance as yf
        import pandas as pd

        ticker = yf.Ticker(symbol)
        # Fetch 1 day of 5-min bars; yfinance returns in market timezone
        hist = ticker.history(period="2d", interval="5m", auto_adjust=True)
        if hist.empty:
            return []

        # Normalize index to UTC
        if hist.index.tzinfo is None:
            hist.index = hist.index.tz_localize("America/New_York").tz_convert("UTC")
        else:
            hist.index = hist.index.tz_convert("UTC")

        # Filter to session_date in ET
        session_dt_et = datetime.strptime(session_date, "%Y-%m-%d").replace(tzinfo=_ET)
        session_dt_utc = session_dt_et.astimezone(_UTC)
        next_dt_utc = session_dt_utc + timedelta(days=1)

        filtered = hist[(hist.index >= session_dt_utc) & (hist.index < next_dt_utc)]
        return list(zip(filtered.index.to_pydatetime(), filtered["Close"].tolist()))
    except Exception as exc:
        logger.warning("ORB forward perf: yfinance fetch for %s failed — %s", symbol, exc)
        return []


def _lookup_bar_close(bars: list, target_ts: datetime) -> Optional[float]:
    """
    Find the closing price of the bar whose open time is closest to (but not after)
    target_ts.  Returns None if no suitable bar exists.
    """
    best_price = None
    best_delta = timedelta(days=999)
    for ts, close in bars:
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.replace(tzinfo=_UTC)
        delta = target_ts - ts
        if timedelta(0) <= delta < best_delta:
            best_delta = delta
            best_price = close
    # Only accept bars within 10 minutes of target
    if best_delta > timedelta(minutes=10):
        return None
    return best_price


def _to_utc(ts) -> datetime:
    if ts is None:
        return datetime.now(tz=_UTC)
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    if hasattr(ts, "tzinfo") and ts.tzinfo is None:
        ts = ts.replace(tzinfo=_ET)
    return ts.astimezone(_UTC)
