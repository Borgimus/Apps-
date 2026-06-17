"""
Liquidity sweep detector.

A liquidity sweep is a sharp, temporary move beyond a session high/low that
is quickly rejected — a classic ICT pattern indicating smart-money accumulation
or distribution.

Objective definitions used here
────────────────────────────────
1. SWEEP: price trades beyond a level by >= min_sweep_ticks * tick_size.
2. REJECTION: within the same candle OR within `rejection_candles` subsequent
   candles, price CLOSES back inside the session range by at least
   `rejection_close_pct` of the extension.

Where "extension" = |sweep_price - level|.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Optional

import pandas as pd

from .config import ICTConfig
from .session_calculator import SessionLevels

logger = logging.getLogger(__name__)


class SweepType(str, Enum):
    ASIAN_HIGH = "ASIAN_HIGH"
    ASIAN_LOW = "ASIAN_LOW"
    LONDON_HIGH = "LONDON_HIGH"
    LONDON_LOW = "LONDON_LOW"


@dataclass
class SweepEvent:
    """A detected liquidity sweep event."""

    symbol: str
    timestamp: datetime              # timestamp of the sweep candle
    level_type: SweepType
    level_price: float               # the session level that was swept
    sweep_price: float               # the extreme price reached during the sweep
    extension: float                 # |sweep_price - level_price|
    rejection_confirmed: bool        # True when close-back-inside criterion met
    rejection_candle_index: Optional[int]  # bar index where rejection confirmed
    sweep_candle_index: int          # bar index of the sweep candle
    direction: str                   # "bearish" (sweep high) | "bullish" (sweep low)

    def is_valid_entry_signal(self) -> bool:
        return self.rejection_confirmed

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "level_type": self.level_type.value,
            "level_price": self.level_price,
            "sweep_price": self.sweep_price,
            "extension": round(self.extension, 6),
            "rejection_confirmed": self.rejection_confirmed,
            "rejection_candle_index": self.rejection_candle_index,
            "sweep_candle_index": self.sweep_candle_index,
            "direction": self.direction,
        }


class LiquiditySweepDetector:
    """
    Detect liquidity sweeps against session levels.

    Parameters
    ----------
    config : ICTConfig
        Strategy configuration containing sweep/rejection parameters.
    """

    def __init__(self, config: ICTConfig):
        self._cfg = config

    # ── Public API ────────────────────────────────────────────────────────────

    def detect_sweeps(
        self,
        bars: pd.DataFrame,
        session_levels: SessionLevels,
        symbol: str = "",
    ) -> List[SweepEvent]:
        """
        Scan bars for sweeps of the provided session levels.

        Parameters
        ----------
        bars : pd.DataFrame
            1-minute OHLCV with UTC-aware DatetimeIndex.
        session_levels : SessionLevels
            The Asian / London high/low to monitor.
        symbol : str
            Instrument ticker (for labelling).

        Returns
        -------
        List[SweepEvent] in chronological order.
        """
        bars = self._normalise(bars)
        if bars.empty or not session_levels.is_complete():
            return []

        events: List[SweepEvent] = []

        levels = [
            (SweepType.ASIAN_HIGH, session_levels.asian_high, True),
            (SweepType.ASIAN_LOW, session_levels.asian_low, False),
            (SweepType.LONDON_HIGH, session_levels.london_high, True),
            (SweepType.LONDON_LOW, session_levels.london_low, False),
        ]

        for level_type, level_price, is_high in levels:
            if level_price is None:
                continue
            events.extend(
                self._scan_level(bars, symbol, level_type, level_price, is_high)
            )

        events.sort(key=lambda e: e.sweep_candle_index)
        logger.debug(
            "Found %d sweep events for %s (%s)", len(events), symbol, session_levels.date
        )
        return events

    def detect_sweeps_multi_session(
        self,
        bars: pd.DataFrame,
        session_levels_list: List[SessionLevels],
        symbol: str = "",
    ) -> List[SweepEvent]:
        """Convenience: scan bars against multiple session-level sets."""
        all_events: List[SweepEvent] = []
        for sl in session_levels_list:
            all_events.extend(self.detect_sweeps(bars, sl, symbol))
        all_events.sort(key=lambda e: e.sweep_candle_index)
        return all_events

    # ── Core detection logic ──────────────────────────────────────────────────

    def _scan_level(
        self,
        bars: pd.DataFrame,
        symbol: str,
        level_type: SweepType,
        level_price: float,
        is_high: bool,  # True → we are watching for an upside sweep
    ) -> List[SweepEvent]:
        """Iterate over every bar and emit sweep events for one level."""
        min_ext = self._cfg.min_sweep_price
        rej_candles = self._cfg.rejection_candles
        rej_pct = self._cfg.rejection_close_pct
        events: List[SweepEvent] = []

        highs = bars["high"].values
        lows = bars["low"].values
        closes = bars["close"].values
        n = len(bars)

        already_swept: set = set()  # avoid duplicate events on same level

        for i in range(n):
            if i in already_swept:
                continue

            if is_high:
                # Upside sweep: high > level by >= min_ext
                if highs[i] <= level_price + min_ext:
                    continue
                sweep_price = highs[i]
                extension = sweep_price - level_price
            else:
                # Downside sweep: low < level by >= min_ext
                if lows[i] >= level_price - min_ext:
                    continue
                sweep_price = lows[i]
                extension = level_price - sweep_price

            # Check for rejection: close back inside the range
            rejection_idx, rejection_confirmed = self._check_rejection(
                bars=bars,
                sweep_i=i,
                level_price=level_price,
                extension=extension,
                is_high=is_high,
                max_candles=rej_candles,
                rej_pct=rej_pct,
                highs=highs,
                lows=lows,
                closes=closes,
            )

            direction = "bearish" if is_high else "bullish"
            ts = bars.index[i]
            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()

            event = SweepEvent(
                symbol=symbol,
                timestamp=ts,
                level_type=level_type,
                level_price=level_price,
                sweep_price=float(sweep_price),
                extension=float(extension),
                rejection_confirmed=rejection_confirmed,
                rejection_candle_index=rejection_idx,
                sweep_candle_index=i,
                direction=direction,
            )
            events.append(event)

            # Mark the next few bars as part of this sweep to avoid re-triggering
            for k in range(i, min(i + rej_candles + 1, n)):
                already_swept.add(k)

        return events

    def _check_rejection(
        self,
        bars: pd.DataFrame,
        sweep_i: int,
        level_price: float,
        extension: float,
        is_high: bool,
        max_candles: int,
        rej_pct: float,
        highs,
        lows,
        closes,
    ):
        """
        Return (candle_index_of_rejection | None, confirmed: bool).

        Rejection is confirmed when a candle's close satisfies:
          For high sweep: close <= level_price + extension * (1 - rej_pct)
          For low sweep:  close >= level_price - extension * (1 - rej_pct)

        In plain English: the close must be at least rej_pct of the way back
        inside the range from the sweep extreme.
        """
        n = len(closes)
        # The threshold close must be inside the range by rej_pct of the extension
        if is_high:
            # close must be <= level_price + extension*(1-rej_pct)
            threshold = level_price + extension * (1.0 - rej_pct)
        else:
            threshold = level_price - extension * (1.0 - rej_pct)

        # Check the sweep candle itself first (same-bar rejection)
        if is_high:
            if closes[sweep_i] <= threshold:
                return sweep_i, True
        else:
            if closes[sweep_i] >= threshold:
                return sweep_i, True

        # Then check subsequent candles
        for j in range(sweep_i + 1, min(sweep_i + max_candles + 1, n)):
            if is_high:
                if closes[j] <= threshold:
                    return j, True
            else:
                if closes[j] >= threshold:
                    return j, True

        return None, False

    @staticmethod
    def _normalise(bars: pd.DataFrame) -> pd.DataFrame:
        df = bars.copy()
        df.columns = [c.lower() for c in df.columns]
        if not hasattr(df.index, "tz") or df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        elif str(df.index.tz) != "UTC":
            df.index = df.index.tz_convert("UTC")
        return df
