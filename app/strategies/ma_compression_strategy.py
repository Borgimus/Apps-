"""
Moving Average Compression Breakout Strategy.

Logic
─────
1. Compute fast EMA(8) and slow EMA(21).
2. Compression: abs(fast - slow) / slow < compression_threshold_pct
   — the two MAs are tightly clustered, indicating a coiling market.
3. After N bars of compression, the first bar that closes decisively above
   the slow EMA = LONG breakout; close below slow EMA = SHORT.
4. Requires that the breakout candle range is at least 1.5× average range.
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from .strategy_base import Signal, SignalDirection, StrategyBase


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


class MACompressionStrategy(StrategyBase):

    @property
    def name(self) -> str:
        return "MA Compression Breakout"

    def __init__(self, params: Dict[str, Any] | None = None):
        super().__init__("ma_compression", params)
        self._fast_period: int = self.params.get("fast_period", 8)
        self._slow_period: int = self.params.get("slow_period", 21)
        self._compression_threshold: float = self.params.get(
            "compression_threshold_pct", 0.005
        )
        # Minimum bars of compression before a breakout counts
        self._min_compression_bars: int = self.params.get("min_compression_bars", 3)

    def generate_signals(self, bars: pd.DataFrame, symbol: str) -> List[Signal]:
        min_rows = max(self._fast_period, self._slow_period) + self._min_compression_bars + 5
        if not self.validate_bars(bars, min_rows=min_rows):
            return []

        bars = bars.copy()
        bars.columns = bars.columns.str.lower()
        bars["fast"] = _ema(bars["close"], self._fast_period)
        bars["slow"] = _ema(bars["close"], self._slow_period)
        bars["spread_pct"] = (bars["fast"] - bars["slow"]).abs() / bars["slow"]
        bars["range"] = bars["high"] - bars["low"]
        bars["avg_range"] = bars["range"].rolling(14).mean()
        bars = bars.dropna()

        signals: List[Signal] = []
        compressed = 0

        for i in range(1, len(bars)):
            row = bars.iloc[i]
            prev = bars.iloc[i - 1]

            if prev["spread_pct"] < self._compression_threshold:
                compressed += 1
            else:
                compressed = 0

            if compressed >= self._min_compression_bars:
                large_bar = row["range"] >= 1.5 * row["avg_range"]

                if row["close"] > row["slow"] and prev["close"] <= prev["slow"] and large_bar:
                    signals.append(
                        Signal(
                            strategy_id=self.strategy_id,
                            symbol=symbol,
                            direction=SignalDirection.LONG,
                            timestamp=bars.index[i].to_pydatetime(),
                            price=float(row["close"]),
                            confidence=0.65,
                            notes=(
                                f"MA compression breakout LONG | "
                                f"fast={row['fast']:.2f} slow={row['slow']:.2f} "
                                f"compressed={compressed}bars"
                            ),
                            metadata={
                                "fast_ema": float(row["fast"]),
                                "slow_ema": float(row["slow"]),
                                "spread_pct": float(row["spread_pct"]),
                                "compressed_bars": compressed,
                            },
                        )
                    )
                    compressed = 0

                elif row["close"] < row["slow"] and prev["close"] >= prev["slow"] and large_bar:
                    signals.append(
                        Signal(
                            strategy_id=self.strategy_id,
                            symbol=symbol,
                            direction=SignalDirection.SHORT,
                            timestamp=bars.index[i].to_pydatetime(),
                            price=float(row["close"]),
                            confidence=0.65,
                            notes=(
                                f"MA compression breakdown SHORT | "
                                f"fast={row['fast']:.2f} slow={row['slow']:.2f} "
                                f"compressed={compressed}bars"
                            ),
                            metadata={
                                "fast_ema": float(row["fast"]),
                                "slow_ema": float(row["slow"]),
                                "spread_pct": float(row["spread_pct"]),
                                "compressed_bars": compressed,
                            },
                        )
                    )
                    compressed = 0

        return signals
