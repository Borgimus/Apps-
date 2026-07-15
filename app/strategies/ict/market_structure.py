"""
Objective market structure engine.

Definitions used
────────────────
Swing High : bars[i].high > bars[i-n].high  AND  bars[i].high > bars[i+n].high
             for every k in 1..n (strict local maximum over the lookback window)
Swing Low  : symmetric

HH / HL / LH / LL : determined by comparing consecutive swing highs / lows.

BOS (Break of Structure)
  Bullish BOS : close crosses above the most recent confirmed swing high
  Bearish BOS : close crosses below the most recent confirmed swing low

CHOCH (Change of Character)
  After an uptrend (latest structure is HH+HL): bearish BOS = CHoCH
  After a downtrend (latest structure is LH+LL): bullish BOS = CHoCH
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import ICTConfig

logger = logging.getLogger(__name__)


class MSEventType(str, Enum):
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"
    HIGHER_HIGH = "HH"
    HIGHER_LOW = "HL"
    LOWER_HIGH = "LH"
    LOWER_LOW = "LL"
    BOS_BULLISH = "BOS_BULLISH"
    BOS_BEARISH = "BOS_BEARISH"
    CHOCH_BULLISH = "CHOCH_BULLISH"
    CHOCH_BEARISH = "CHOCH_BEARISH"


@dataclass
class MarketStructureEvent:
    type: MSEventType
    price: float
    timestamp: datetime
    candle_index: int
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "price": self.price,
            "timestamp": self.timestamp.isoformat(),
            "candle_index": self.candle_index,
            "note": self.note,
        }


@dataclass
class SwingPoint:
    is_high: bool
    price: float
    timestamp: datetime
    candle_index: int
    label: Optional[MSEventType] = None  # HH/HL/LH/LL once assigned


class MarketStructureEngine:
    """
    Identifies swing points and market structure events from 1-minute bars.

    Parameters
    ----------
    config : ICTConfig
    """

    def __init__(self, config: ICTConfig):
        self._cfg = config

    # ── Public API ────────────────────────────────────────────────────────────

    def analyse(self, bars: pd.DataFrame) -> List[MarketStructureEvent]:
        """
        Full market structure analysis: swings → HH/HL/LH/LL → BOS/CHoCH.

        Parameters
        ----------
        bars : pd.DataFrame  1-minute OHLCV, UTC-aware index.

        Returns
        -------
        List[MarketStructureEvent] in chronological order.
        """
        bars = self._normalise(bars)
        if len(bars) < self._cfg.swing_lookback * 2 + 1:
            return []

        swing_highs, swing_lows = self._find_swings(bars)

        events: List[MarketStructureEvent] = []

        # Add raw swing events
        for sh in swing_highs:
            events.append(
                MarketStructureEvent(
                    type=MSEventType.SWING_HIGH,
                    price=sh.price,
                    timestamp=sh.timestamp,
                    candle_index=sh.candle_index,
                )
            )
        for sl in swing_lows:
            events.append(
                MarketStructureEvent(
                    type=MSEventType.SWING_LOW,
                    price=sl.price,
                    timestamp=sl.timestamp,
                    candle_index=sl.candle_index,
                )
            )

        # Label HH/HL/LH/LL
        hh_hl_events = self._label_highs(swing_highs, bars)
        hl_ll_events = self._label_lows(swing_lows, bars)
        events.extend(hh_hl_events)
        events.extend(hl_ll_events)

        # BOS and CHoCH
        bos_events = self._detect_bos_choch(bars, swing_highs, swing_lows)
        events.extend(bos_events)

        events.sort(key=lambda e: e.candle_index)
        logger.debug("Market structure: %d events on %d bars", len(events), len(bars))
        return events

    def get_swing_highs(self, bars: pd.DataFrame) -> List[SwingPoint]:
        bars = self._normalise(bars)
        highs, _ = self._find_swings(bars)
        return highs

    def get_swing_lows(self, bars: pd.DataFrame) -> List[SwingPoint]:
        bars = self._normalise(bars)
        _, lows = self._find_swings(bars)
        return lows

    # ── Swing detection ───────────────────────────────────────────────────────

    def _find_swings(
        self, bars: pd.DataFrame
    ) -> Tuple[List[SwingPoint], List[SwingPoint]]:
        n = self._cfg.swing_lookback
        highs_arr = bars["high"].values
        lows_arr = bars["low"].values
        N = len(bars)

        swing_highs: List[SwingPoint] = []
        swing_lows: List[SwingPoint] = []

        for i in range(n, N - n):
            h = highs_arr[i]
            l = lows_arr[i]

            # Strict local maximum
            if all(h > highs_arr[i - k] for k in range(1, n + 1)) and all(
                h > highs_arr[i + k] for k in range(1, n + 1)
            ):
                ts = bars.index[i]
                if hasattr(ts, "to_pydatetime"):
                    ts = ts.to_pydatetime()
                swing_highs.append(SwingPoint(is_high=True, price=float(h), timestamp=ts, candle_index=i))

            # Strict local minimum
            if all(l < lows_arr[i - k] for k in range(1, n + 1)) and all(
                l < lows_arr[i + k] for k in range(1, n + 1)
            ):
                ts = bars.index[i]
                if hasattr(ts, "to_pydatetime"):
                    ts = ts.to_pydatetime()
                swing_lows.append(SwingPoint(is_high=False, price=float(l), timestamp=ts, candle_index=i))

        return swing_highs, swing_lows

    # ── HH / HL / LH / LL labelling ──────────────────────────────────────────

    def _label_highs(
        self, swing_highs: List[SwingPoint], bars: pd.DataFrame
    ) -> List[MarketStructureEvent]:
        events: List[MarketStructureEvent] = []
        for i in range(1, len(swing_highs)):
            prev = swing_highs[i - 1]
            curr = swing_highs[i]
            if curr.price > prev.price:
                label = MSEventType.HIGHER_HIGH
            else:
                label = MSEventType.LOWER_HIGH
            curr.label = label
            events.append(
                MarketStructureEvent(
                    type=label,
                    price=curr.price,
                    timestamp=curr.timestamp,
                    candle_index=curr.candle_index,
                    note=f"prev={prev.price:.4f}",
                )
            )
        return events

    def _label_lows(
        self, swing_lows: List[SwingPoint], bars: pd.DataFrame
    ) -> List[MarketStructureEvent]:
        events: List[MarketStructureEvent] = []
        for i in range(1, len(swing_lows)):
            prev = swing_lows[i - 1]
            curr = swing_lows[i]
            if curr.price > prev.price:
                label = MSEventType.HIGHER_LOW
            else:
                label = MSEventType.LOWER_LOW
            curr.label = label
            events.append(
                MarketStructureEvent(
                    type=label,
                    price=curr.price,
                    timestamp=curr.timestamp,
                    candle_index=curr.candle_index,
                    note=f"prev={prev.price:.4f}",
                )
            )
        return events

    # ── BOS / CHoCH ───────────────────────────────────────────────────────────

    def _detect_bos_choch(
        self,
        bars: pd.DataFrame,
        swing_highs: List[SwingPoint],
        swing_lows: List[SwingPoint],
    ) -> List[MarketStructureEvent]:
        """
        Scan every bar for break of structure or change of character.

        BOS bullish : close > most recent swing high
        BOS bearish : close < most recent swing low
        CHoCH       : BOS against the prevailing trend direction
        """
        closes = bars["close"].values
        N = len(bars)
        events: List[MarketStructureEvent] = []

        # Pointers into swing arrays
        sh_ptr = 0
        sl_ptr = 0
        last_sh: Optional[SwingPoint] = None
        last_sl: Optional[SwingPoint] = None
        prev_bos: Optional[MSEventType] = None  # track trend direction

        for i in range(N):
            # Advance swing pointers to bars whose candle_index < i
            while sh_ptr < len(swing_highs) and swing_highs[sh_ptr].candle_index < i:
                last_sh = swing_highs[sh_ptr]
                sh_ptr += 1
            while sl_ptr < len(swing_lows) and swing_lows[sl_ptr].candle_index < i:
                last_sl = swing_lows[sl_ptr]
                sl_ptr += 1

            close = closes[i]
            ts = bars.index[i]
            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()

            # Bullish BOS / CHoCH
            if last_sh is not None and close > last_sh.price:
                if prev_bos == MSEventType.BOS_BEARISH:
                    evt_type = MSEventType.CHOCH_BULLISH
                else:
                    evt_type = MSEventType.BOS_BULLISH
                events.append(
                    MarketStructureEvent(
                        type=evt_type,
                        price=close,
                        timestamp=ts,
                        candle_index=i,
                        note=f"broke swing_high={last_sh.price:.4f}@{last_sh.candle_index}",
                    )
                )
                prev_bos = MSEventType.BOS_BULLISH
                last_sh = None  # consumed

            # Bearish BOS / CHoCH
            if last_sl is not None and close < last_sl.price:
                if prev_bos == MSEventType.BOS_BULLISH:
                    evt_type = MSEventType.CHOCH_BEARISH
                else:
                    evt_type = MSEventType.BOS_BEARISH
                events.append(
                    MarketStructureEvent(
                        type=evt_type,
                        price=close,
                        timestamp=ts,
                        candle_index=i,
                        note=f"broke swing_low={last_sl.price:.4f}@{last_sl.candle_index}",
                    )
                )
                prev_bos = MSEventType.BOS_BEARISH
                last_sl = None  # consumed

        return events

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _normalise(bars: pd.DataFrame) -> pd.DataFrame:
        df = bars.copy()
        df.columns = [c.lower() for c in df.columns]
        if not hasattr(df.index, "tz") or df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        elif str(df.index.tz) != "UTC":
            df.index = df.index.tz_convert("UTC")
        return df
