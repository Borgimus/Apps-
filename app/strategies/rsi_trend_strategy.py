"""
RSI + Trend Filter Strategy.

Logic
─────
1. Compute RSI(14) and EMA(50) on the input bars.
2. Trend filter: price above EMA → only take LONG (call) signals.
               price below EMA → only take SHORT (put) signals.
3. Long  entry: RSI crosses up from oversold (< rsi_oversold).
4. Short entry: RSI crosses down from overbought (> rsi_overbought).
5. Exit  signal: RSI crosses back to the mid-band (50).
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from .strategy_base import Signal, SignalDirection, StrategyBase


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


class RSITrendStrategy(StrategyBase):

    @property
    def name(self) -> str:
        return "RSI + Trend Filter"

    def __init__(self, params: Dict[str, Any] | None = None):
        super().__init__("rsi_trend", params)
        self._rsi_period: int = self.params.get("rsi_period", 14)
        self._rsi_oversold: float = self.params.get("rsi_oversold", 35)
        self._rsi_overbought: float = self.params.get("rsi_overbought", 65)
        self._trend_ema_period: int = self.params.get("trend_ema_period", 50)

    def generate_signals(self, bars: pd.DataFrame, symbol: str) -> List[Signal]:
        min_rows = max(self._rsi_period, self._trend_ema_period) + 5
        if not self.validate_bars(bars, min_rows=min_rows):
            return []

        bars = bars.copy()
        bars.columns = bars.columns.str.lower()
        bars["rsi"] = _rsi(bars["close"], self._rsi_period)
        bars["ema"] = _ema(bars["close"], self._trend_ema_period)
        bars = bars.dropna(subset=["rsi", "ema"])

        signals: List[Signal] = []
        rsi = bars["rsi"].values
        ema = bars["ema"].values
        close = bars["close"].values
        idx = bars.index

        for i in range(1, len(bars)):
            trend_bullish = close[i] > ema[i]
            trend_bearish = close[i] < ema[i]

            # RSI cross up from oversold
            if rsi[i - 1] < self._rsi_oversold and rsi[i] >= self._rsi_oversold and trend_bullish:
                signals.append(
                    Signal(
                        strategy_id=self.strategy_id,
                        symbol=symbol,
                        direction=SignalDirection.LONG,
                        timestamp=idx[i].to_pydatetime(),
                        price=float(close[i]),
                        confidence=0.6 + (self._rsi_oversold - rsi[i - 1]) / 100,
                        notes=f"RSI oversold bounce | RSI={rsi[i]:.1f} EMA={ema[i]:.2f}",
                        metadata={"rsi": float(rsi[i]), "ema": float(ema[i])},
                    )
                )

            # RSI cross down from overbought
            elif rsi[i - 1] > self._rsi_overbought and rsi[i] <= self._rsi_overbought and trend_bearish:
                signals.append(
                    Signal(
                        strategy_id=self.strategy_id,
                        symbol=symbol,
                        direction=SignalDirection.SHORT,
                        timestamp=idx[i].to_pydatetime(),
                        price=float(close[i]),
                        confidence=0.6 + (rsi[i - 1] - self._rsi_overbought) / 100,
                        notes=f"RSI overbought fade | RSI={rsi[i]:.1f} EMA={ema[i]:.2f}",
                        metadata={"rsi": float(rsi[i]), "ema": float(ema[i])},
                    )
                )

        return signals
