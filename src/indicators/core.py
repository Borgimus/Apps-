"""Deterministic indicator math. Stdlib only; no lookahead."""
from __future__ import annotations

from typing import Mapping, Sequence

Number = float
Bar = Mapping[str, float]


def _require(seq: Sequence, n: int, name: str) -> None:
    if n <= 0:
        raise ValueError(f"{name}: period must be positive, got {n}")
    if len(seq) < n:
        raise ValueError(f"{name}: need >= {n} values, got {len(seq)}")


def sma(values: Sequence[Number], period: int) -> float:
    """Simple moving average of the most recent ``period`` values."""
    _require(values, period, "sma")
    window = values[-period:]
    return sum(window) / period


def sma_series(values: Sequence[Number], period: int) -> list[float]:
    """SMA at every index where a full window exists (aligned to the window end)."""
    _require(values, period, "sma_series")
    out: list[float] = []
    for end in range(period, len(values) + 1):
        window = values[end - period:end]
        out.append(sum(window) / period)
    return out


def ema_series(values: Sequence[Number], period: int) -> list[float]:
    """EMA seeded with the first value, k = 2/(period+1)."""
    _require(values, period, "ema_series")
    k = 2.0 / (period + 1.0)
    ema_val = float(values[0])
    out = [ema_val]
    for v in values[1:]:
        ema_val = v * k + ema_val * (1.0 - k)
        out.append(ema_val)
    return out


def ema(values: Sequence[Number], period: int) -> float:
    """Final EMA value."""
    return ema_series(values, period)[-1]


def true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def adr_pct(bars: Sequence[Bar], period: int) -> float:
    """Average Daily Range percent over ``period`` bars.

    ADR%(N) = mean((high_i - low_i) / close_{i-1} * 100).
    Requires ``period + 1`` bars (needs a previous close for the first term).
    """
    if period <= 0:
        raise ValueError("adr_pct: period must be positive")
    if len(bars) < period + 1:
        raise ValueError(f"adr_pct: need >= {period + 1} bars, got {len(bars)}")
    window = bars[-period:]
    prev_closes = [bars[i - 1]["close"] for i in range(len(bars) - period, len(bars))]
    total = 0.0
    for bar, prev_close in zip(window, prev_closes):
        if prev_close <= 0:
            raise ValueError("adr_pct: non-positive previous close")
        total += (bar["high"] - bar["low"]) / prev_close * 100.0
    return total / period


def atr(bars: Sequence[Bar], period: int) -> float:
    """Wilder ATR over ``period`` bars. Requires ``period + 1`` bars."""
    if period <= 0:
        raise ValueError("atr: period must be positive")
    if len(bars) < period + 1:
        raise ValueError(f"atr: need >= {period + 1} bars, got {len(bars)}")
    trs: list[float] = []
    for i in range(1, len(bars)):
        trs.append(true_range(bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]))
    # Wilder smoothing seeded with the first ``period`` TRs.
    atr_val = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period
    return atr_val


def atr_pct(bars: Sequence[Bar], period: int) -> float:
    last_close = bars[-1]["close"]
    if last_close <= 0:
        raise ValueError("atr_pct: non-positive last close")
    return atr(bars, period) / last_close * 100.0


def dollar_volume(bars: Sequence[Bar], period: int) -> float:
    """Average daily dollar volume = mean(close_i * volume_i) over last N bars."""
    _require(bars, period, "dollar_volume")
    window = bars[-period:]
    return sum(b["close"] * b["volume"] for b in window) / period


def slope_pct(ma_values: Sequence[Number], lookback: int) -> float:
    """Normalized slope: percent change of a moving-average series over ``lookback``.

    slope% = (ma_t - ma_{t-lookback}) / ma_{t-lookback} * 100.
    This is scale-free and replaces any notion of literal chart angle.
    """
    if lookback <= 0:
        raise ValueError("slope_pct: lookback must be positive")
    if len(ma_values) < lookback + 1:
        raise ValueError(f"slope_pct: need >= {lookback + 1} MA values, got {len(ma_values)}")
    past = ma_values[-1 - lookback]
    now = ma_values[-1]
    if past == 0:
        raise ValueError("slope_pct: zero reference MA value")
    return (now - past) / past * 100.0


def lookback_return_pct(closes: Sequence[Number], lookback_bars: int) -> float:
    """Percent return over ``lookback_bars`` (uses close now vs close lookback_bars ago).

    Uses only past data; no future bars are referenced.
    """
    if lookback_bars <= 0:
        raise ValueError("lookback_return_pct: lookback must be positive")
    if len(closes) < lookback_bars + 1:
        raise ValueError(
            f"lookback_return_pct: need >= {lookback_bars + 1} closes, got {len(closes)}"
        )
    past = closes[-1 - lookback_bars]
    now = closes[-1]
    if past <= 0:
        raise ValueError("lookback_return_pct: non-positive reference close")
    return (now - past) / past * 100.0
