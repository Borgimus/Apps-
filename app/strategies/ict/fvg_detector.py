"""
Fair Value Gap (FVG) detector.

Standard ICT FVG definitions
──────────────────────────────
  Bullish FVG : candle[i-2].high < candle[i].low   → gap above candle[i-2]
  Bearish FVG : candle[i-2].low  > candle[i].high  → gap below candle[i-2]

(Using the 3-bar convention: bar 0 = anchor, bar 1 = impulse, bar 2 = current.
The gap is between bar 0's high/low and bar 2's low/high.)

Fill tracking: once a FVG is created the midpoint and fill percentage are
updated as price enters the gap.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional

import pandas as pd

from .config import ICTConfig

logger = logging.getLogger(__name__)


class FVGType(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


@dataclass
class FVG:
    """A single Fair Value Gap."""

    type: FVGType
    upper_price: float   # top of the gap
    lower_price: float   # bottom of the gap
    timestamp: datetime  # timestamp of candle[i] (the third candle)
    candle_index: int    # index of the third bar in the source DataFrame
    filled_pct: float = 0.0   # 0.0 → unfilled, 1.0 → fully filled
    is_valid: bool = True     # False once gap is > 100 % filled (violated)

    @property
    def midpoint(self) -> float:
        return (self.upper_price + self.lower_price) / 2.0

    @property
    def size(self) -> float:
        return self.upper_price - self.lower_price

    def check_fill(self, current_price: float) -> float:
        """
        Compute how much of the FVG has been filled by current_price.

        For a BULLISH FVG (gap from lower_price to upper_price):
          - Price enters from the top (upper_price) going down.
          - fill = (upper_price - current_price) / size
        For a BEARISH FVG:
          - Price enters from the bottom (lower_price) going up.
          - fill = (current_price - lower_price) / size

        Returns a float in [0, 1+] (can exceed 1 if price violates the gap).
        """
        if self.size <= 0:
            return 0.0
        if self.type == FVGType.BULLISH:
            fill = (self.upper_price - current_price) / self.size
        else:
            fill = (current_price - self.lower_price) / self.size
        return max(0.0, fill)

    def update_fill(self, current_price: float) -> None:
        """Update filled_pct and is_valid in-place."""
        self.filled_pct = self.check_fill(current_price)
        if self.filled_pct >= 1.0:
            self.is_valid = False

    def is_price_inside(self, price: float) -> bool:
        return self.lower_price <= price <= self.upper_price

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "upper_price": self.upper_price,
            "lower_price": self.lower_price,
            "midpoint": self.midpoint,
            "size": round(self.size, 6),
            "timestamp": self.timestamp.isoformat(),
            "candle_index": self.candle_index,
            "filled_pct": round(self.filled_pct, 4),
            "is_valid": self.is_valid,
        }


class FVGDetector:
    """
    Detect and track Fair Value Gaps in a bar series.

    Parameters
    ----------
    config : ICTConfig
    """

    def __init__(self, config: ICTConfig):
        self._cfg = config

    # ── Public API ────────────────────────────────────────────────────────────

    def detect_fvgs(
        self,
        bars: pd.DataFrame,
        start_index: int = 0,
        end_index: Optional[int] = None,
    ) -> List[FVG]:
        """
        Scan bars[start_index:end_index] for FVGs.

        Requires at least 3 bars.  FVGs smaller than min_fvg_size are skipped.

        Returns list of FVG (newest last).
        """
        bars = self._normalise(bars)
        end_index = end_index if end_index is not None else len(bars)
        start_index = max(start_index, 2)  # need at least bar 0, 1, 2

        results: List[FVG] = []
        highs = bars["high"].values
        lows = bars["low"].values
        min_size = self._cfg.min_fvg_size

        for i in range(start_index, end_index):
            # -- Bullish FVG: gap between bar[i-2].high and bar[i].low
            upper_b = lows[i]
            lower_b = highs[i - 2]
            if upper_b > lower_b and (upper_b - lower_b) >= min_size:
                ts = bars.index[i]
                if hasattr(ts, "to_pydatetime"):
                    ts = ts.to_pydatetime()
                results.append(
                    FVG(
                        type=FVGType.BULLISH,
                        upper_price=float(upper_b),
                        lower_price=float(lower_b),
                        timestamp=ts,
                        candle_index=i,
                    )
                )

            # -- Bearish FVG: gap between bar[i-2].low and bar[i].high
            lower_be = highs[i]
            upper_be = lows[i - 2]
            if upper_be > lower_be and (upper_be - lower_be) >= min_size:
                ts = bars.index[i]
                if hasattr(ts, "to_pydatetime"):
                    ts = ts.to_pydatetime()
                results.append(
                    FVG(
                        type=FVGType.BEARISH,
                        upper_price=float(upper_be),
                        lower_price=float(lower_be),
                        timestamp=ts,
                        candle_index=i,
                    )
                )

        logger.debug("Detected %d FVGs in range [%d, %d)", len(results), start_index, end_index)
        return results

    def find_fvgs_after_sweep(
        self,
        bars: pd.DataFrame,
        sweep_candle_index: int,
        lookback: int,
        fvg_type: FVGType,
    ) -> List[FVG]:
        """
        Return FVGs of the requested type in the window immediately following
        the sweep candle (up to lookback bars back, inclusive of sweep candle).

        Logic: after a HIGH sweep we want BEARISH FVGs; after a LOW sweep
        we want BULLISH FVGs.
        """
        start = max(2, sweep_candle_index - lookback)
        end = sweep_candle_index + 1  # include the sweep candle itself
        fvgs = self.detect_fvgs(bars, start_index=start, end_index=end)
        matching = [f for f in fvgs if f.type == fvg_type]
        return matching

    def update_fvg_fills(
        self, fvgs: List[FVG], current_price: float
    ) -> List[FVG]:
        """Update fill percentages in-place, return only still-valid FVGs."""
        for fvg in fvgs:
            fvg.update_fill(current_price)
        return [f for f in fvgs if f.is_valid]

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
