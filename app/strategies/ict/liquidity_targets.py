"""
Liquidity target identification.

Liquidity lives above swing highs (buy-stops) and below swing lows (sell-stops).
ICT traders refer to these pools when projecting targets.

Types of liquidity levels identified
─────────────────────────────────────
1. Previous day high / low (from daily bars)
2. Session highs / lows (Asian, London)
3. Equal highs / equal lows (two swing points within equal_threshold_price)
4. Swing highs / lows from the market structure engine

Strength rating (1-3)
  1 – single swing point
  2 – session level or previous-day level
  3 – equal high/low (two or more confluent swing points)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple

import pandas as pd

from .config import ICTConfig
from .market_structure import MarketStructureEngine, SwingPoint
from .session_calculator import SessionLevels

logger = logging.getLogger(__name__)


class LiquidityLevelType(str, Enum):
    PREVIOUS_DAY_HIGH = "PREVIOUS_DAY_HIGH"
    PREVIOUS_DAY_LOW = "PREVIOUS_DAY_LOW"
    ASIAN_HIGH = "ASIAN_HIGH"
    ASIAN_LOW = "ASIAN_LOW"
    LONDON_HIGH = "LONDON_HIGH"
    LONDON_LOW = "LONDON_LOW"
    EQUAL_HIGH = "EQUAL_HIGH"
    EQUAL_LOW = "EQUAL_LOW"
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"


@dataclass
class LiquidityLevel:
    price: float
    type: LiquidityLevelType
    strength: int           # 1=weak, 2=moderate, 3=strong
    timestamp: datetime
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "type": self.type.value,
            "strength": self.strength,
            "timestamp": self.timestamp.isoformat(),
            "note": self.note,
        }


class LiquidityTargetFinder:
    """
    Identify all relevant liquidity pools for a given symbol and bar series.

    Parameters
    ----------
    config : ICTConfig
    """

    def __init__(self, config: ICTConfig):
        self._cfg = config
        self._ms = MarketStructureEngine(config)

    # ── Public API ────────────────────────────────────────────────────────────

    def find_targets(
        self,
        minute_bars: pd.DataFrame,
        session_levels_list: List[SessionLevels],
        daily_bars: Optional[pd.DataFrame] = None,
    ) -> List[LiquidityLevel]:
        """
        Collect all liquidity levels from all available sources.

        Parameters
        ----------
        minute_bars : 1-min OHLCV, UTC-aware index
        session_levels_list : computed SessionLevels for the relevant days
        daily_bars : optional daily OHLCV for previous-day high/low

        Returns
        -------
        List[LiquidityLevel] sorted by price descending (highs first).
        """
        minute_bars = self._normalise(minute_bars)
        levels: List[LiquidityLevel] = []

        # 1. Previous day high/low
        if daily_bars is not None and not daily_bars.empty:
            levels.extend(self._previous_day_levels(daily_bars))

        # 2. Session highs/lows
        for sl in session_levels_list:
            levels.extend(self._session_levels(sl))

        # 3. Swing highs/lows from market structure
        swing_highs = self._ms.get_swing_highs(minute_bars)
        swing_lows = self._ms.get_swing_lows(minute_bars)
        levels.extend(self._swing_levels(swing_highs, swing_lows))

        # 4. Equal highs / equal lows (from the swing points)
        levels.extend(self._equal_levels(swing_highs, is_high=True))
        levels.extend(self._equal_levels(swing_lows, is_high=False))

        # De-duplicate: collapse levels within one tick of each other
        levels = self._deduplicate(levels)

        levels.sort(key=lambda l: l.price, reverse=True)
        logger.debug("Found %d liquidity levels", len(levels))
        return levels

    def nearest_target(
        self,
        levels: List[LiquidityLevel],
        current_price: float,
        direction: str,  # "bullish" | "bearish"
        min_strength: int = 1,
    ) -> Optional[LiquidityLevel]:
        """
        Return the closest qualifying liquidity level in the trade direction.

        For a bullish trade, look for the nearest level ABOVE current price.
        For a bearish trade, look for the nearest level BELOW current price.
        """
        filtered = [l for l in levels if l.strength >= min_strength]
        if direction == "bullish":
            candidates = [l for l in filtered if l.price > current_price]
            if not candidates:
                return None
            return min(candidates, key=lambda l: l.price - current_price)
        else:
            candidates = [l for l in filtered if l.price < current_price]
            if not candidates:
                return None
            return max(candidates, key=lambda l: l.price)

    # ── Level builders ────────────────────────────────────────────────────────

    def _previous_day_levels(self, daily_bars: pd.DataFrame) -> List[LiquidityLevel]:
        daily = self._normalise(daily_bars)
        if len(daily) < 2:
            return []
        prev_day = daily.iloc[-2]
        ts = daily.index[-1]
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        return [
            LiquidityLevel(
                price=float(prev_day["high"]),
                type=LiquidityLevelType.PREVIOUS_DAY_HIGH,
                strength=2,
                timestamp=ts,
                note="PDH",
            ),
            LiquidityLevel(
                price=float(prev_day["low"]),
                type=LiquidityLevelType.PREVIOUS_DAY_LOW,
                strength=2,
                timestamp=ts,
                note="PDL",
            ),
        ]

    def _session_levels(self, sl: SessionLevels) -> List[LiquidityLevel]:
        levels: List[LiquidityLevel] = []
        ts = sl.timestamp
        mapping = [
            (sl.asian_high, LiquidityLevelType.ASIAN_HIGH, "Asian High"),
            (sl.asian_low, LiquidityLevelType.ASIAN_LOW, "Asian Low"),
            (sl.london_high, LiquidityLevelType.LONDON_HIGH, "London High"),
            (sl.london_low, LiquidityLevelType.LONDON_LOW, "London Low"),
        ]
        for price, ltype, note in mapping:
            if price is not None:
                levels.append(
                    LiquidityLevel(price=price, type=ltype, strength=2, timestamp=ts, note=note)
                )
        return levels

    def _swing_levels(
        self, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint]
    ) -> List[LiquidityLevel]:
        levels: List[LiquidityLevel] = []
        for sh in swing_highs:
            levels.append(
                LiquidityLevel(
                    price=sh.price,
                    type=LiquidityLevelType.SWING_HIGH,
                    strength=1,
                    timestamp=sh.timestamp,
                    note=f"Swing High @{sh.candle_index}",
                )
            )
        for sl in swing_lows:
            levels.append(
                LiquidityLevel(
                    price=sl.price,
                    type=LiquidityLevelType.SWING_LOW,
                    strength=1,
                    timestamp=sl.timestamp,
                    note=f"Swing Low @{sl.candle_index}",
                )
            )
        return levels

    def _equal_levels(
        self, swing_points: List[SwingPoint], is_high: bool
    ) -> List[LiquidityLevel]:
        """Find pairs of swing points within equal_threshold_price of each other."""
        threshold = self._cfg.equal_threshold_price
        level_type = LiquidityLevelType.EQUAL_HIGH if is_high else LiquidityLevelType.EQUAL_LOW
        results: List[LiquidityLevel] = []
        used: set = set()

        for i in range(len(swing_points)):
            if i in used:
                continue
            for j in range(i + 1, len(swing_points)):
                if j in used:
                    continue
                pi = swing_points[i].price
                pj = swing_points[j].price
                if abs(pi - pj) <= threshold:
                    avg_price = (pi + pj) / 2.0
                    results.append(
                        LiquidityLevel(
                            price=avg_price,
                            type=level_type,
                            strength=3,
                            timestamp=swing_points[j].timestamp,
                            note=f"Equal {'High' if is_high else 'Low'} ({pi:.4f} ≈ {pj:.4f})",
                        )
                    )
                    used.add(i)
                    used.add(j)
                    break

        return results

    def _deduplicate(self, levels: List[LiquidityLevel]) -> List[LiquidityLevel]:
        """Collapse levels within one tick_size of each other; keep highest strength."""
        tick = self._cfg.tick_size
        out: List[LiquidityLevel] = []
        levels_sorted = sorted(levels, key=lambda l: l.price)
        for level in levels_sorted:
            merged = False
            for existing in out:
                if abs(existing.price - level.price) <= tick:
                    # Keep the one with higher strength; average the price
                    if level.strength > existing.strength:
                        existing.price = level.price
                        existing.strength = level.strength
                        existing.type = level.type
                        existing.note = level.note
                    merged = True
                    break
            if not merged:
                out.append(level)
        return out

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
