"""Pure-Python, dependency-free indicators with explicit formulas.

Every function is deterministic and uses only past/present bars (no lookahead).
Prices are plain floats; a "bar" is a mapping with keys
``open, high, low, close, volume`` unless noted.

Formulas (authoritative — also mirrored in config/strategy.yaml and docs):

    SMA(N)      = mean(close[-N:])
    EMA(N)      = close[0] seeded, then EMA_t = close_t*k + EMA_{t-1}*(1-k), k=2/(N+1)
    ADR%(N)     = mean((high_i - low_i) / close_{i-1} * 100  for the last N bars)
    ATR(N)      = Wilder-smoothed mean of true range;
                  TR_i = max(high_i-low_i, |high_i-prev_close|, |low_i-prev_close|)
    ATR%(N)     = ATR(N) / last_close * 100
    dollar_vol  = mean(close_i * volume_i, last N bars)
    slope%(k)   = (ma_t - ma_{t-k}) / ma_{t-k} * 100
"""
from .core import (
    sma,
    ema,
    sma_series,
    ema_series,
    adr_pct,
    atr,
    atr_pct,
    true_range,
    dollar_volume,
    slope_pct,
    lookback_return_pct,
)

__all__ = [
    "sma",
    "ema",
    "sma_series",
    "ema_series",
    "adr_pct",
    "atr",
    "atr_pct",
    "true_range",
    "dollar_volume",
    "slope_pct",
    "lookback_return_pct",
]
