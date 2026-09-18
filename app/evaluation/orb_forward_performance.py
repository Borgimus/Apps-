"""
ORB forward performance — post-session computation.

For every ORB signal in signal_bridge (traded or not), fetches execution-grade
5-minute Alpaca bars for the underlying and computes the hypothetical price and
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
    """Return Alpaca (timestamp_utc, close) tuples for the session date."""
    if broker is None or not hasattr(broker, "get_stock_bars"):
        logger.warning(
            "ORB forward perf: Alpaca stock-bar source unavailable for %s", symbol
        )
        return []

    session_start_et = datetime.strptime(session_date, "%Y-%m-%d").replace(
        tzinfo=_ET
    )
    session_end_et = session_start_et + timedelta(days=1)
    try:
        return await broker.get_stock_bars(
            symbol,
            start=session_start_et.astimezone(_UTC),
            end=session_end_et.astimezone(_UTC),
            timeframe="5Min",
        )
    except Exception as exc:
        logger.warning(
            "ORB forward perf: Alpaca bars fetch for %s failed — %s", symbol, exc
        )
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
