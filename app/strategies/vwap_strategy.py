"""
VWAP Reclaim / Rejection Strategy.

Logic
─────
Reclaim  : Price falls below VWAP, then reclaims it with a close ABOVE.
            Confirmation requires N consecutive bars holding above VWAP.
            → LONG signal.

Rejection: Price rises above VWAP, then fails and closes BELOW.
            → SHORT signal.

Entry proximity filter: Only fire when price is within proximity_pct of VWAP,
avoiding late/extended moves.
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from ..data.yfinance_data import YFinanceDataSource
from .strategy_base import Signal, SignalDirection, StrategyBase


class VWAPReclaimStrategy(StrategyBase):

    @property
    def name(self) -> str:
        return "VWAP Reclaim/Rejection"

    def __init__(self, params: Dict[str, Any] | None = None):
        super().__init__("vwap_reclaim", params)
        self._proximity_pct: float = self.params.get("proximity_pct", 0.002)
        self._confirmation_bars: int = self.params.get("confirmation_bars", 2)

    @property
    def min_bars_required(self) -> int:
        return self._confirmation_bars + 5

    def generate_signals(self, bars: pd.DataFrame, symbol: str) -> List[Signal]:
        if not self.validate_bars(bars, min_rows=self.min_bars_required):
            return []

        bars = bars.copy()
        bars.columns = bars.columns.str.lower()

        # Compute daily VWAP — reset each calendar day.
        # Use an explicit loop for pandas 3.x compatibility; groupby().apply()
        # changed its return shape in pandas 3 when the function returns a Series.
        bars["_date"] = bars.index.date
        vwap_values = pd.Series(index=bars.index, dtype=float)
        for _, group in bars.groupby("_date"):
            typical = (group["high"] + group["low"] + group["close"]) / 3
            vwap_val = (typical * group["volume"]).cumsum() / group["volume"].cumsum()
            vwap_values[group.index] = vwap_val
        bars["vwap"] = vwap_values

        signals: List[Signal] = []

        for day, day_bars in bars.groupby("_date"):
            day_signals = self._scan_day(day_bars, symbol)
            signals.extend(day_signals)

        return signals

    def _scan_day(self, day_bars: pd.DataFrame, symbol: str) -> List[Signal]:
        signals: List[Signal] = []
        close = day_bars["close"].values
        vwap = day_bars["vwap"].values
        timestamps = day_bars.index

        n = len(close)
        for i in range(1, n - self._confirmation_bars):
            price = close[i]
            v = vwap[i]
            if v == 0:
                continue

            pct_from_vwap = abs(price - v) / v

            # Reclaim: was below, now above, within proximity
            was_below = close[i - 1] < vwap[i - 1]
            now_above = price > v
            if was_below and now_above and pct_from_vwap <= self._proximity_pct:
                # Require confirmation_bars of staying above
                confirmed = all(
                    close[i + k] > vwap[i + k]
                    for k in range(1, self._confirmation_bars + 1)
                    if i + k < n
                )
                if confirmed:
                    signals.append(
                        Signal(
                            strategy_id=self.strategy_id,
                            symbol=symbol,
                            direction=SignalDirection.LONG,
                            timestamp=timestamps[i].to_pydatetime(),
                            price=float(price),
                            confidence=0.65,
                            notes=f"VWAP reclaim @ {v:.2f}",
                            metadata={"vwap": float(v), "proximity_pct": float(pct_from_vwap)},
                        )
                    )

            # Rejection: was above, now below, within proximity
            was_above = close[i - 1] > vwap[i - 1]
            now_below = price < v
            if was_above and now_below and pct_from_vwap <= self._proximity_pct:
                confirmed = all(
                    close[i + k] < vwap[i + k]
                    for k in range(1, self._confirmation_bars + 1)
                    if i + k < n
                )
                if confirmed:
                    signals.append(
                        Signal(
                            strategy_id=self.strategy_id,
                            symbol=symbol,
                            direction=SignalDirection.SHORT,
                            timestamp=timestamps[i].to_pydatetime(),
                            price=float(price),
                            confidence=0.65,
                            notes=f"VWAP rejection @ {v:.2f}",
                            metadata={"vwap": float(v), "proximity_pct": float(pct_from_vwap)},
                        )
                    )

        return signals
