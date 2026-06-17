"""
Pydantic request/response models for the ICT API.

These are the API-layer DTOs — they are separate from the SQLAlchemy ORM
models in app/api/models.py and the strategy-internal dataclasses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ── Session models ─────────────────────────────────────────────────────────────

class SessionWindowModel(BaseModel):
    start_hour: int = Field(..., ge=0, le=23)
    end_hour: int = Field(..., ge=0, le=23)


class SessionLevelsResponse(BaseModel):
    symbol: str
    date: str
    timestamp: str
    asian_high: Optional[float]
    asian_low: Optional[float]
    london_high: Optional[float]
    london_low: Optional[float]
    is_complete: bool


# ── FVG models ─────────────────────────────────────────────────────────────────

class FVGResponse(BaseModel):
    type: str
    upper_price: float
    lower_price: float
    midpoint: float
    size: float
    timestamp: str
    candle_index: int
    filled_pct: float
    is_valid: bool


# ── Sweep models ───────────────────────────────────────────────────────────────

class SweepEventResponse(BaseModel):
    symbol: str
    timestamp: str
    level_type: str
    level_price: float
    sweep_price: float
    extension: float
    rejection_confirmed: bool
    rejection_candle_index: Optional[int]
    sweep_candle_index: int
    direction: str


# ── Signal models ──────────────────────────────────────────────────────────────

class LiquidityLevelResponse(BaseModel):
    price: float
    type: str
    strength: int
    timestamp: str
    note: str


class ICTSignalResponse(BaseModel):
    strategy_id: str
    symbol: str
    direction: str
    timestamp: str
    price: float
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_amount: float
    position_size: float
    confidence: float
    notes: str
    fvg: Optional[FVGResponse]
    sweep_event: Optional[SweepEventResponse]
    liquidity_target: Optional[LiquidityLevelResponse]


class SignalsFilterParams(BaseModel):
    symbol: Optional[str] = None
    direction: Optional[str] = None
    min_confidence: float = 0.0
    limit: int = Field(default=50, ge=1, le=500)


# ── Market structure models ────────────────────────────────────────────────────

class MarketStructureEventResponse(BaseModel):
    type: str
    price: float
    timestamp: str
    candle_index: int
    note: str


class MarketStructureResponse(BaseModel):
    symbol: str
    events: List[MarketStructureEventResponse]
    computed_at: str


# ── Backtest models ────────────────────────────────────────────────────────────

class ICTBacktestRequest(BaseModel):
    symbol: str
    start: Optional[str] = None
    end: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    starting_equity: float = Field(default=100_000.0, gt=0)
    commission_per_unit: float = Field(default=0.0, ge=0)
    slippage_ticks: float = Field(default=0.0, ge=0)


class ICTTradeRecordResponse(BaseModel):
    symbol: str
    direction: str
    entry_time: Optional[str]
    exit_time: Optional[str]
    entry_price: float
    exit_price: Optional[float]
    stop_loss: float
    take_profit: float
    position_size: float
    risk_amount: float
    pnl: float
    rr_achieved: float
    trade_duration_minutes: int
    exit_reason: str
    fvg_type: str
    sweep_type: str


class ICTBacktestResultResponse(BaseModel):
    task_id: str
    status: str                    # "pending" | "running" | "complete" | "error"
    symbol: str
    start_date: Optional[str]
    end_date: Optional[str]
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    long_win_rate: float = 0.0
    short_win_rate: float = 0.0
    avg_rr: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    total_pnl: float = 0.0
    total_return: float = 0.0
    monthly_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    monthly_pnl: Dict[str, float] = Field(default_factory=dict)
    trades: List[ICTTradeRecordResponse] = Field(default_factory=list)
    error: Optional[str] = None


# ── Scanner models ─────────────────────────────────────────────────────────────

class ScanResultResponse(BaseModel):
    symbol: str
    signal: Optional[ICTSignalResponse]
    confidence: float
    session_levels: Optional[SessionLevelsResponse]
    active_fvgs: List[FVGResponse]
    scanned_at: str
    error: Optional[str]


class ScannerResponse(BaseModel):
    results: List[ScanResultResponse]
    symbols_scanned: int
    signals_found: int
    scanned_at: str


# ── Config models ──────────────────────────────────────────────────────────────

class ICTConfigResponse(BaseModel):
    asian_session: SessionWindowModel
    london_session: SessionWindowModel
    tick_size: float
    min_sweep_ticks: float
    rejection_candles: int
    rejection_close_pct: float
    min_fvg_size: float
    fvg_fill_pct_entry: float
    fvg_lookback_bars: int
    swing_lookback: int
    equal_threshold_ticks: float
    sl_buffer_ticks: float
    exit_mode: str
    fixed_rr_ratio: float
    account_size: float
    risk_per_trade_pct: float
    max_daily_loss_pct: float
    max_trades_per_day: int
    trading_start_hour: int
    stop_trading_hour: int
    min_confidence: float


class ICTConfigUpdateRequest(BaseModel):
    """All fields optional; only provided fields are updated."""
    tick_size: Optional[float] = None
    min_sweep_ticks: Optional[float] = None
    rejection_candles: Optional[int] = None
    rejection_close_pct: Optional[float] = None
    min_fvg_size: Optional[float] = None
    fvg_fill_pct_entry: Optional[float] = None
    fvg_lookback_bars: Optional[int] = None
    swing_lookback: Optional[int] = None
    sl_buffer_ticks: Optional[float] = None
    exit_mode: Optional[Literal["fixed_rr", "liquidity_target", "major_structure", "hybrid"]] = None
    fixed_rr_ratio: Optional[float] = None
    account_size: Optional[float] = None
    risk_per_trade_pct: Optional[float] = None
    max_daily_loss_pct: Optional[float] = None
    max_trades_per_day: Optional[int] = None
    trading_start_hour: Optional[int] = None
    stop_trading_hour: Optional[int] = None
    min_confidence: Optional[float] = None


# ── WebSocket models ───────────────────────────────────────────────────────────

class WSSignalMessage(BaseModel):
    type: Literal["ict_signal", "ict_sweep", "ict_fvg", "heartbeat"]
    data: Dict[str, Any]
    timestamp: str
