"""
Opening Range Breakout (ORB) Strategy.

Logic
─────
1. Compute the opening range: high and low of the first N minutes.
2. After the range is established, watch for a candle close ABOVE the high
   (long signal) or BELOW the low (short signal).
3. Require volume confirmation: breakout bar volume > average of prior bars.
4. Ignore signals less than min_range_pts wide (avoids thin pre-market ranges).

Best suited for: SPY, QQQ on high-volume days.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, Dict, List

import pandas as pd

from .strategy_base import Signal, SignalDirection, StrategyBase


class OpeningRangeBreakoutStrategy(StrategyBase):

    @property
    def name(self) -> str:
        return "Opening Range Breakout"

    def __init__(self, params: Dict[str, Any] | None = None):
        super().__init__("orb", params)
        self._range_minutes: int = self.params.get("range_minutes", 15)
        self._min_range_pts: float = self.params.get("min_range_pts", 0.5)
        self._volume_confirmation: bool = self.params.get("volume_confirmation", True)

    def generate_signals(self, bars: pd.DataFrame, symbol: str) -> List[Signal]:
        if not self.validate_bars(bars, min_rows=self._range_minutes + 2):
            return []

        bars = bars.copy()
        bars.columns = bars.columns.str.lower()

        # Work in ET for session-based slicing
        if bars.index.tz is not None:
            try:
                bars.index = bars.index.tz_convert("America/New_York")
            except Exception:
                pass

        # Group by trading day
        bars["_date"] = bars.index.date
        signals: List[Signal] = []

        for day, day_bars in bars.groupby("_date"):
            day_signals = self._process_day(day_bars, symbol)
            signals.extend(day_signals)

        return signals

    def _process_day(self, day_bars: pd.DataFrame, symbol: str) -> List[Signal]:
        signals: List[Signal] = []
        market_open = time(9, 30)
        range_end_offset = pd.Timedelta(minutes=self._range_minutes)

        # Opening range bars
        first_bar_time = day_bars.index[0].time() if not day_bars.empty else None
        if first_bar_time is None or first_bar_time > time(9, 45):
            return []  # no early-session bars

        range_cutoff = day_bars.index[0] + range_end_offset
        range_bars = day_bars[day_bars.index <= range_cutoff]
        if range_bars.empty:
            return []

        or_high = range_bars["high"].max()
        or_low = range_bars["low"].min()
        or_range = or_high - or_low

        if or_range < self._min_range_pts:
            self.logger.debug(
                "ORB: range too narrow (%.2f < %.2f) for %s on %s",
                or_range,
                self._min_range_pts,
                symbol,
                day_bars.index[0].date(),
            )
            return []

        avg_vol = range_bars["volume"].mean()

        # Scan post-range bars for breakout
        post_range = day_bars[day_bars.index > range_cutoff]
        for ts, row in post_range.iterrows():
            vol_ok = not self._volume_confirmation or row["volume"] > avg_vol

            if row["close"] > or_high and vol_ok:
                signals.append(
                    Signal(
                        strategy_id=self.strategy_id,
                        symbol=symbol,
                        direction=SignalDirection.LONG,
                        timestamp=ts.to_pydatetime(),
                        price=float(row["close"]),
                        confidence=min(0.9, (row["close"] - or_high) / or_range + 0.6),
                        notes=f"ORB breakout above {or_high:.2f} | range={or_range:.2f}",
                        metadata={
                            "or_high": or_high,
                            "or_low": or_low,
                            "or_range": or_range,
                        },
                    )
                )
                break  # one signal per day per direction

            if row["close"] < or_low and vol_ok:
                signals.append(
                    Signal(
                        strategy_id=self.strategy_id,
                        symbol=symbol,
                        direction=SignalDirection.SHORT,
                        timestamp=ts.to_pydatetime(),
                        price=float(row["close"]),
                        confidence=min(0.9, (or_low - row["close"]) / or_range + 0.6),
                        notes=f"ORB breakdown below {or_low:.2f} | range={or_range:.2f}",
                        metadata={
                            "or_high": or_high,
                            "or_low": or_low,
                            "or_range": or_range,
                        },
                    )
                )
                break

        return signals
