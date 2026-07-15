"""
ICT Strategy configuration — all parameters with defaults.

This is the single source of truth for every tunable in the ICT
Liquidity Sweep & FVG Reversal strategy.  Parameters can be overridden
at construction time via the params dict passed to ICTStrategy.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional


class SessionWindow(BaseModel):
    """UTC hour range for a named session."""
    start_hour: int = Field(..., ge=0, le=23)
    end_hour: int = Field(..., ge=0, le=23)

    @field_validator("end_hour")
    @classmethod
    def end_after_start(cls, v, info):
        # allow overnight windows (start > end) e.g. 22:00 → 02:00
        return v

    def contains_hour(self, hour: int) -> bool:
        if self.start_hour <= self.end_hour:
            return self.start_hour <= hour < self.end_hour
        # overnight: e.g. 22–02
        return hour >= self.start_hour or hour < self.end_hour


class ICTConfig(BaseModel):
    """All ICT strategy parameters with production-ready defaults."""

    # ── Session windows (UTC) ─────────────────────────────────────────────────
    asian_session: SessionWindow = Field(
        default_factory=lambda: SessionWindow(start_hour=0, end_hour=6)
    )
    london_session: SessionWindow = Field(
        default_factory=lambda: SessionWindow(start_hour=3, end_hour=8)
    )

    # ── Tick / pip sizing ─────────────────────────────────────────────────────
    # One "tick" expressed in price units.
    # ES futures: 0.25  |  NQ: 0.25  |  Forex pairs: 0.0001  |  Stocks: 0.01
    tick_size: float = Field(default=0.25, gt=0)

    # ── Liquidity sweep detection ─────────────────────────────────────────────
    # Minimum excursion beyond session level to count as a sweep (in ticks)
    min_sweep_ticks: float = Field(default=2.0, gt=0)

    # How many candles AFTER the sweep candle are eligible to confirm rejection
    rejection_candles: int = Field(default=3, ge=1)

    # What fraction of the extension must the close retrace to confirm rejection
    # e.g. 0.50 → close must retrace at least 50 % back inside the range
    rejection_close_pct: float = Field(default=0.5, ge=0.01, le=1.0)

    # ── FVG detection ─────────────────────────────────────────────────────────
    # Minimum gap size in price units (0 = accept any non-zero gap)
    min_fvg_size: float = Field(default=0.0, ge=0.0)

    # Enter when price has filled at least this fraction of the FVG
    fvg_fill_pct_entry: float = Field(default=0.0, ge=0.0, le=1.0)

    # How many bars after the sweep to search for a qualifying FVG
    fvg_lookback_bars: int = Field(default=20, ge=3)

    # ── Market structure ──────────────────────────────────────────────────────
    swing_lookback: int = Field(default=5, ge=2)

    # Equal high/low threshold in ticks
    equal_threshold_ticks: float = Field(default=2.0, gt=0)

    # ── Stop-loss ─────────────────────────────────────────────────────────────
    # Buffer beyond the sweep extreme, in ticks
    sl_buffer_ticks: float = Field(default=2.0, ge=0)

    # ── Take-profit / exit mode ───────────────────────────────────────────────
    exit_mode: Literal["fixed_rr", "liquidity_target", "major_structure", "hybrid"] = (
        "fixed_rr"
    )
    fixed_rr_ratio: float = Field(default=2.0, gt=0)  # 1:2 R:R

    # ── Position sizing ───────────────────────────────────────────────────────
    account_size: float = Field(default=100_000.0, gt=0)
    risk_per_trade_pct: float = Field(default=0.01, gt=0, le=0.1)  # 1 %

    # ── Daily risk guards ─────────────────────────────────────────────────────
    max_daily_loss_pct: float = Field(default=0.02, gt=0, le=0.5)
    max_trades_per_day: int = Field(default=3, ge=1)

    # ── Trading hours (UTC) ───────────────────────────────────────────────────
    # Only generate entry signals between these hours
    trading_start_hour: int = Field(default=14, ge=0, le=23)  # 09:00 NY
    stop_trading_hour: int = Field(default=21, ge=0, le=23)   # 16:00 NY

    # ── Minimum signal confidence (0-1) to emit ───────────────────────────────
    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)

    @property
    def min_sweep_price(self) -> float:
        return self.min_sweep_ticks * self.tick_size

    @property
    def sl_buffer_price(self) -> float:
        return self.sl_buffer_ticks * self.tick_size

    @property
    def equal_threshold_price(self) -> float:
        return self.equal_threshold_ticks * self.tick_size

    def risk_amount(self) -> float:
        return self.account_size * self.risk_per_trade_pct

    @classmethod
    def from_dict(cls, d: dict) -> "ICTConfig":
        """Build from a flat param dict, handling nested SessionWindow."""
        data = dict(d)
        for key in ("asian_session", "london_session"):
            if key in data and isinstance(data[key], dict):
                data[key] = SessionWindow(**data[key])
        return cls(**data)
