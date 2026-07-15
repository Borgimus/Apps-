"""
ICT strategy FastAPI router.

Mounts under the main FastAPI app at /api/ict.

Endpoints
──────────
GET  /api/ict/sessions/{symbol}          Current session levels
GET  /api/ict/signals                    Recent ICT signals (filterable)
POST /api/ict/backtest                   Trigger async backtest → task_id
GET  /api/ict/backtest/{task_id}         Poll backtest result
GET  /api/ict/scanner                    Run scanner on configured symbols
GET  /api/ict/config                     Get current ICT config
PUT  /api/ict/config                     Update ICT config
WS   /ws/ict/signals                     Real-time signal stream
GET  /api/ict/market-structure/{symbol}  Market structure events
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
import warnings
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..data.data_fetcher import fetch_1min_bars
from ..strategies.ict import (
    ICTStrategy,
    MarketStructureEngine,
    SessionCalculator,
)
from ..strategies.ict.config import ICTConfig as ICTStrategyConfig
from .ict_models import (
    FVGResponse,
    ICTBacktestRequest,
    ICTBacktestResultResponse,
    ICTConfigResponse,
    ICTConfigUpdateRequest,
    ICTSignalResponse,
    LiquidityLevelResponse,
    MarketStructureEventResponse,
    MarketStructureResponse,
    ScanResultResponse,
    ScannerResponse,
    SessionLevelsResponse,
    SessionWindowModel,
    SweepEventResponse,
)
from .models import (
    AsyncSessionLocal,
    ICTBacktestResult as DBICTBacktestResult,
    ICTConfig as DBICTConfig,
    ICTTrade as DBICTTrade,
    get_db,
)

logger = logging.getLogger(__name__)

# ── In-memory state ───────────────────────────────────────────────────────────
# Active ICT config (loaded from DB on first request; default otherwise)
_active_config: ICTStrategyConfig = ICTStrategyConfig()

# In-memory task registry: task_id → ICTBacktestResult dict
_backtest_tasks: Dict[str, Dict[str, Any]] = {}

# Signal broadcast manager (shared with main ws)
class _ICTWSManager:
    def __init__(self):
        self.connections: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)
        logger.debug("ICT WS connected; total=%d", len(self.connections))

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)

    async def broadcast(self, data: dict):
        payload = json.dumps(data)
        dead = []
        for ws in self.connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.connections.remove(ws)


_ws_manager = _ICTWSManager()

# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/ict", tags=["ICT Strategy"])
ws_router = APIRouter(tags=["ICT WebSocket"])


# ── Helper: safe data fetch (Alpaca → yfinance fallback) ─────────────────────

def _fetch_bars_silent(symbol: str, days: int = 7):
    settings = get_settings()
    if settings.alpaca_api_key and settings.alpaca_secret_key:
        from ..data.alpaca_data import fetch_alpaca_bars, is_supported
        if is_supported(symbol):
            bars = fetch_alpaca_bars(
                symbol,
                api_key=settings.alpaca_api_key,
                secret_key=settings.alpaca_secret_key,
                days=days,
                data_feed=settings.alpaca_data_feed,
                data_url=settings.alpaca_data_url,
            )
            if not bars.empty:
                return bars
            logger.info("Alpaca returned no bars for %s — falling back to yfinance", symbol)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fetch_1min_bars(symbol, warn=False)


# ── Session levels ─────────────────────────────────────────────────────────────

@router.get("/sessions/{symbol}", response_model=SessionLevelsResponse)
async def get_session_levels(symbol: str):
    """Return the most recent session high/low levels for a symbol."""
    bars = _fetch_bars_silent(symbol)
    if bars.empty:
        raise HTTPException(status_code=404, detail=f"No bar data available for {symbol}")

    calc = SessionCalculator(_active_config)
    levels = calc.get_current_session_levels(bars)
    if levels is None:
        raise HTTPException(status_code=404, detail=f"Could not compute session levels for {symbol}")

    return SessionLevelsResponse(
        symbol=symbol,
        date=levels.date,
        timestamp=levels.timestamp.isoformat(),
        asian_high=levels.asian_high,
        asian_low=levels.asian_low,
        london_high=levels.london_high,
        london_low=levels.london_low,
        is_complete=levels.is_complete(),
    )


# ── Signals ────────────────────────────────────────────────────────────────────

@router.get("/signals", response_model=List[ICTSignalResponse])
async def get_signals(
    symbol: Optional[str] = Query(None),
    direction: Optional[str] = Query(None),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=50, ge=1, le=500),
):
    """
    Return recently generated ICT signals.

    If symbol is provided, only signals for that symbol are returned.
    Signals are generated live from the latest bar data.
    """
    settings = get_settings()
    symbols_to_scan = [symbol] if symbol else settings.watchlist[:5]

    all_signals: List[ICTSignalResponse] = []
    strategy = ICTStrategy(params=_active_config.model_dump())

    for sym in symbols_to_scan:
        bars = _fetch_bars_silent(sym)
        if bars.empty:
            continue
        try:
            raw_signals = strategy.generate_signals(bars, sym)
        except Exception as exc:
            logger.warning("Signal generation failed for %s: %s", sym, exc)
            continue

        for sig in raw_signals:
            if sig.confidence < min_confidence:
                continue
            if direction and sig.direction.value != direction:
                continue
            all_signals.append(_signal_to_response(sig))

    all_signals.sort(key=lambda s: s.timestamp, reverse=True)
    return all_signals[:limit]


def _signal_to_response(sig) -> ICTSignalResponse:
    fvg_resp = None
    if sig.fvg:
        f = sig.fvg
        fvg_resp = FVGResponse(
            type=f.type.value,
            upper_price=f.upper_price,
            lower_price=f.lower_price,
            midpoint=f.midpoint,
            size=f.size,
            timestamp=f.timestamp.isoformat(),
            candle_index=f.candle_index,
            filled_pct=f.filled_pct,
            is_valid=f.is_valid,
        )
    sweep_resp = None
    if sig.sweep_event:
        s = sig.sweep_event
        sweep_resp = SweepEventResponse(
            symbol=s.symbol,
            timestamp=s.timestamp.isoformat(),
            level_type=s.level_type.value,
            level_price=s.level_price,
            sweep_price=s.sweep_price,
            extension=s.extension,
            rejection_confirmed=s.rejection_confirmed,
            rejection_candle_index=s.rejection_candle_index,
            sweep_candle_index=s.sweep_candle_index,
            direction=s.direction,
        )
    liq_resp = None
    if sig.liquidity_target:
        lt = sig.liquidity_target
        liq_resp = LiquidityLevelResponse(
            price=lt.price,
            type=lt.type.value,
            strength=lt.strength,
            timestamp=lt.timestamp.isoformat(),
            note=lt.note,
        )
    ts = sig.timestamp
    ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    return ICTSignalResponse(
        strategy_id=sig.strategy_id,
        symbol=sig.symbol,
        direction=sig.direction.value,
        timestamp=ts_str,
        price=sig.price,
        entry_price=sig.entry_price,
        stop_loss=sig.stop_loss,
        take_profit=sig.take_profit,
        risk_amount=sig.risk_amount,
        position_size=sig.position_size,
        confidence=sig.confidence,
        notes=sig.notes,
        fvg=fvg_resp,
        sweep_event=sweep_resp,
        liquidity_target=liq_resp,
    )


# ── Backtest ───────────────────────────────────────────────────────────────────

@router.post("/backtest")
async def trigger_backtest(req: ICTBacktestRequest, db: AsyncSession = Depends(get_db)):
    """
    Trigger an async ICT backtest.  Returns immediately with a task_id.
    Poll GET /api/ict/backtest/{task_id} for results.
    """
    task_id = str(uuid.uuid4())

    # Create DB record with pending status
    db_row = DBICTBacktestResult(
        task_id=task_id,
        symbol=req.symbol,
        start_date=req.start,
        end_date=req.end,
        status="pending",
        strategy_params_json=json.dumps(req.params),
    )
    db.add(db_row)
    await db.commit()

    _backtest_tasks[task_id] = {"status": "pending", "symbol": req.symbol}

    # Fire-and-forget background task
    asyncio.ensure_future(
        _run_backtest_background(task_id, req, db_row.id)
    )

    return {"task_id": task_id, "status": "pending"}


async def _run_backtest_background(task_id: str, req: ICTBacktestRequest, db_row_id: int):
    """Background coroutine that runs the backtest and persists results."""
    from ..backtesting.ict_backtester import ICTBacktester

    _backtest_tasks[task_id]["status"] = "running"

    try:
        loop = asyncio.get_event_loop()

        # Fetch bars in executor
        bars = await loop.run_in_executor(
            None, lambda: _fetch_bars_silent(req.symbol)
        )

        if bars.empty:
            raise ValueError(f"No bar data available for {req.symbol}")

        bt = ICTBacktester(
            params=req.params,
            starting_equity=req.starting_equity,
            commission_per_unit=req.commission_per_unit,
            slippage_ticks=req.slippage_ticks,
        )
        result = await loop.run_in_executor(
            None, lambda: bt.run(bars, req.symbol, req.start, req.end)
        )

        _backtest_tasks[task_id] = {
            "status": "complete",
            "result": result.to_dict(),
            "trades": [t.to_dict() for t in result.trades],
        }

        # Persist to DB
        async with AsyncSessionLocal() as session:
            row = await session.get(DBICTBacktestResult, db_row_id)
            if row:
                d = result.to_dict()
                row.status = "complete"
                row.total_trades = d["total_trades"]
                row.winning_trades = d["winning_trades"]
                row.losing_trades = d["losing_trades"]
                row.win_rate = d["win_rate"]
                row.long_win_rate = d["long_win_rate"]
                row.short_win_rate = d["short_win_rate"]
                row.avg_rr = d["avg_rr"]
                row.profit_factor = d["profit_factor"]
                row.expectancy = d["expectancy"]
                row.total_pnl = d["total_pnl"]
                row.total_return = d["total_return"]
                row.monthly_return = d["monthly_return"]
                row.max_drawdown = d["max_drawdown"]
                row.sharpe_ratio = d["sharpe_ratio"]
                await session.commit()

            # Persist trades
            for trade in result.trades:
                t = DBICTTrade(
                    symbol=trade.symbol,
                    direction=trade.direction,
                    entry_time=trade.entry_time,
                    exit_time=trade.exit_time,
                    entry_price=trade.entry_price,
                    exit_price=trade.exit_price,
                    stop_loss=trade.stop_loss,
                    take_profit=trade.take_profit,
                    position_size=trade.position_size,
                    risk_amount=trade.risk_amount,
                    pnl=trade.pnl,
                    rr_achieved=trade.rr_achieved,
                    trade_duration_minutes=trade.trade_duration_minutes,
                    exit_reason=trade.exit_reason,
                    fvg_type=trade.fvg_type,
                    sweep_type=trade.sweep_type,
                    backtest_result_id=db_row_id,
                )
                session.add(t)
            await session.commit()

    except Exception as exc:
        logger.error("Backtest task %s failed: %s", task_id, exc, exc_info=True)
        _backtest_tasks[task_id] = {"status": "error", "error": str(exc)}
        async with AsyncSessionLocal() as session:
            row = await session.get(DBICTBacktestResult, db_row_id)
            if row:
                row.status = "error"
                row.error_message = str(exc)
                await session.commit()


@router.get("/backtest/{task_id}", response_model=ICTBacktestResultResponse)
async def get_backtest_result(task_id: str):
    """Poll for backtest results by task_id."""
    task = _backtest_tasks.get(task_id)
    if task is None:
        # Check DB
        async with AsyncSessionLocal() as session:
            result_db = (
                await session.execute(
                    select(DBICTBacktestResult).where(DBICTBacktestResult.task_id == task_id)
                )
            ).scalar_one_or_none()
            if result_db is None:
                raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
            return ICTBacktestResultResponse(
                task_id=task_id,
                status=result_db.status or "unknown",
                symbol=result_db.symbol,
                start_date=result_db.start_date,
                end_date=result_db.end_date,
                total_trades=result_db.total_trades or 0,
                winning_trades=result_db.winning_trades or 0,
                losing_trades=result_db.losing_trades or 0,
                win_rate=result_db.win_rate or 0.0,
                error=result_db.error_message,
            )

    status = task.get("status", "unknown")
    if status == "error":
        return ICTBacktestResultResponse(
            task_id=task_id,
            status="error",
            symbol=task.get("symbol", ""),
            error=task.get("error"),
        )
    if status in ("pending", "running"):
        return ICTBacktestResultResponse(
            task_id=task_id,
            status=status,
            symbol=task.get("symbol", ""),
        )

    result_dict = task.get("result", {})
    from ..backtesting.ict_backtester import ICTTradeRecord
    from .ict_models import ICTTradeRecordResponse

    trades_resp = [
        ICTTradeRecordResponse(**t) for t in task.get("trades", [])
    ]
    return ICTBacktestResultResponse(
        task_id=task_id,
        status="complete",
        symbol=result_dict.get("symbol", ""),
        start_date=result_dict.get("start_date"),
        end_date=result_dict.get("end_date"),
        total_trades=result_dict.get("total_trades", 0),
        winning_trades=result_dict.get("winning_trades", 0),
        losing_trades=result_dict.get("losing_trades", 0),
        win_rate=result_dict.get("win_rate", 0.0),
        long_win_rate=result_dict.get("long_win_rate", 0.0),
        short_win_rate=result_dict.get("short_win_rate", 0.0),
        avg_rr=result_dict.get("avg_rr", 0.0),
        profit_factor=result_dict.get("profit_factor", 0.0),
        expectancy=result_dict.get("expectancy", 0.0),
        total_pnl=result_dict.get("total_pnl", 0.0),
        total_return=result_dict.get("total_return", 0.0),
        monthly_return=result_dict.get("monthly_return", 0.0),
        max_drawdown=result_dict.get("max_drawdown", 0.0),
        sharpe_ratio=result_dict.get("sharpe_ratio", 0.0),
        monthly_pnl=result_dict.get("monthly_pnl", {}),
        trades=trades_resp,
    )


# ── Bars (OHLCV for frontend chart) ───────────────────────────────────────────

@router.get("/bars/{symbol}")
async def get_bars(
    symbol: str,
    limit: int = Query(default=300, ge=10, le=2000),
    days: int = Query(default=7, ge=1, le=30),
):
    """Return recent OHLCV 1-minute bars for the frontend chart."""
    bars = _fetch_bars_silent(symbol, days=days)
    if bars.empty:
        return []
    tail = bars.tail(limit)
    return [
        {
            "time": int(ts.timestamp()),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row.get("volume", 0)),
        }
        for ts, row in tail.iterrows()
    ]


# ── Scanner ────────────────────────────────────────────────────────────────────

@router.get("/scanner", response_model=ScannerResponse)
async def run_scanner(
    symbols: Optional[str] = Query(
        None, description="Comma-separated symbols; defaults to watchlist"
    )
):
    """Run the ICT scanner across symbols."""
    settings = get_settings()
    sym_list = (
        [s.strip() for s in symbols.split(",") if s.strip()]
        if symbols
        else settings.watchlist[:5]
    )

    from ..scanner import ICTScanner

    def _fetcher(sym: str):
        return _fetch_bars_silent(sym)

    scanner = ICTScanner(
        symbols=sym_list,
        data_fetcher=_fetcher,
        params=_active_config.model_dump(),
        max_concurrent=4,
    )
    results = await scanner.scan_all()

    now = datetime.now(timezone.utc).isoformat()
    responses = []
    for r in results:
        sl_resp = None
        if r.session_levels:
            sl = r.session_levels
            sl_resp = SessionLevelsResponse(
                symbol=r.symbol,
                date=sl.date,
                timestamp=sl.timestamp.isoformat(),
                asian_high=sl.asian_high,
                asian_low=sl.asian_low,
                london_high=sl.london_high,
                london_low=sl.london_low,
                is_complete=sl.is_complete(),
            )
        fvgs_resp = [
            FVGResponse(
                type=f.type.value,
                upper_price=f.upper_price,
                lower_price=f.lower_price,
                midpoint=f.midpoint,
                size=f.size,
                timestamp=f.timestamp.isoformat(),
                candle_index=f.candle_index,
                filled_pct=f.filled_pct,
                is_valid=f.is_valid,
            )
            for f in r.active_fvgs
        ]
        responses.append(
            ScanResultResponse(
                symbol=r.symbol,
                signal=_signal_to_response(r.signal) if r.signal else None,
                confidence=r.confidence,
                session_levels=sl_resp,
                active_fvgs=fvgs_resp,
                scanned_at=r.scanned_at.isoformat(),
                error=r.error,
            )
        )

    return ScannerResponse(
        results=responses,
        symbols_scanned=len(results),
        signals_found=sum(1 for r in results if r.signal is not None),
        scanned_at=now,
    )


# ── Config ─────────────────────────────────────────────────────────────────────

@router.get("/config", response_model=ICTConfigResponse)
async def get_config():
    """Return the current active ICT strategy configuration."""
    cfg = _active_config
    return ICTConfigResponse(
        asian_session=SessionWindowModel(
            start_hour=cfg.asian_session.start_hour,
            end_hour=cfg.asian_session.end_hour,
        ),
        london_session=SessionWindowModel(
            start_hour=cfg.london_session.start_hour,
            end_hour=cfg.london_session.end_hour,
        ),
        tick_size=cfg.tick_size,
        min_sweep_ticks=cfg.min_sweep_ticks,
        rejection_candles=cfg.rejection_candles,
        rejection_close_pct=cfg.rejection_close_pct,
        min_fvg_size=cfg.min_fvg_size,
        fvg_fill_pct_entry=cfg.fvg_fill_pct_entry,
        fvg_lookback_bars=cfg.fvg_lookback_bars,
        swing_lookback=cfg.swing_lookback,
        equal_threshold_ticks=cfg.equal_threshold_ticks,
        sl_buffer_ticks=cfg.sl_buffer_ticks,
        exit_mode=cfg.exit_mode,
        fixed_rr_ratio=cfg.fixed_rr_ratio,
        account_size=cfg.account_size,
        risk_per_trade_pct=cfg.risk_per_trade_pct,
        max_daily_loss_pct=cfg.max_daily_loss_pct,
        max_trades_per_day=cfg.max_trades_per_day,
        trading_start_hour=cfg.trading_start_hour,
        stop_trading_hour=cfg.stop_trading_hour,
        min_confidence=cfg.min_confidence,
    )


@router.put("/config", response_model=ICTConfigResponse)
async def update_config(req: ICTConfigUpdateRequest, db: AsyncSession = Depends(get_db)):
    """Update the active ICT strategy configuration."""
    global _active_config
    current = _active_config.model_dump()
    updates = req.model_dump(exclude_none=True)
    current.update(updates)
    _active_config = ICTStrategyConfig.from_dict(current)

    # Persist to DB
    existing = (
        await db.execute(select(DBICTConfig).where(DBICTConfig.name == "default"))
    ).scalar_one_or_none()
    if existing is None:
        existing = DBICTConfig(name="default")
        db.add(existing)

    for k, v in updates.items():
        if hasattr(existing, k):
            setattr(existing, k, v)
    await db.commit()

    logger.info("ICT config updated: %s", updates)
    return await get_config()


# ── Market structure ───────────────────────────────────────────────────────────

@router.get("/market-structure/{symbol}", response_model=MarketStructureResponse)
async def get_market_structure(symbol: str, limit: int = Query(default=100, ge=10, le=500)):
    """Return market structure events (swings, BOS, CHoCH) for a symbol."""
    bars = _fetch_bars_silent(symbol)
    if bars.empty:
        raise HTTPException(status_code=404, detail=f"No bar data for {symbol}")

    engine = MarketStructureEngine(_active_config)
    events = engine.analyse(bars)

    now = datetime.now(timezone.utc).isoformat()
    event_responses = [
        MarketStructureEventResponse(
            type=e.type.value,
            price=e.price,
            timestamp=e.timestamp.isoformat(),
            candle_index=e.candle_index,
            note=e.note,
        )
        for e in events[-limit:]
    ]
    return MarketStructureResponse(
        symbol=symbol,
        events=event_responses,
        computed_at=now,
    )


# ── WebSocket signal stream ────────────────────────────────────────────────────

@ws_router.websocket("/ws/ict/signals")
async def ws_ict_signals(websocket: WebSocket):
    """Real-time ICT signal stream.  Sends heartbeat every 30 s."""
    await _ws_manager.connect(websocket)
    try:
        while True:
            # Keep-alive: wait for client message (ping) or 30 s timeout
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send heartbeat
                await websocket.send_text(
                    json.dumps({
                        "type": "heartbeat",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                )
    except WebSocketDisconnect:
        _ws_manager.disconnect(websocket)
    except Exception as exc:
        logger.debug("ICT WS closed: %s", exc)
        _ws_manager.disconnect(websocket)


async def broadcast_ict_signal(signal_data: dict) -> None:
    """Push an ICT signal to all connected WebSocket clients."""
    await _ws_manager.broadcast({
        "type": "signal",
        "data": signal_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# ── Background scanner loop ────────────────────────────────────────────────────

_seen_signal_keys: set[str] = set()  # dedup by (symbol, sweep_type, timestamp-minute)


async def _background_scan_loop(interval_seconds: int = 60) -> None:
    """Periodically run the ICT scanner and broadcast new signals."""
    global _seen_signal_keys
    await asyncio.sleep(10)  # wait for app startup to settle
    logger.info("ICT background scanner started (interval=%ds)", interval_seconds)

    while True:
        try:
            settings = get_settings()
            symbols = settings.watchlist[:5]
            strategy = ICTStrategy(params=_active_config.model_dump())

            for sym in symbols:
                bars = _fetch_bars_silent(sym)
                if bars.empty:
                    continue
                try:
                    raw_signals = strategy.generate_signals(bars, sym)
                except Exception as exc:
                    logger.debug("Scanner error for %s: %s", sym, exc)
                    continue

                for sig in raw_signals:
                    minute_bucket = sig.timestamp[:16] if hasattr(sig, "timestamp") else ""
                    key = f"{sig.symbol}:{sig.sweep_type}:{minute_bucket}"
                    if key in _seen_signal_keys:
                        continue
                    _seen_signal_keys.add(key)
                    if len(_seen_signal_keys) > 500:
                        _seen_signal_keys = set(list(_seen_signal_keys)[-250:])

                    payload = {
                        "id": str(uuid.uuid4()),
                        "symbol": sig.symbol,
                        "direction": sig.direction.value if hasattr(sig.direction, "value") else str(sig.direction),
                        "entry_price": float(sig.entry_price),
                        "stop_loss": float(sig.stop_loss),
                        "take_profit": float(sig.take_profit),
                        "fvg_upper": float(sig.fvg_upper),
                        "fvg_lower": float(sig.fvg_lower),
                        "sweep_type": str(sig.sweep_type),
                        "confidence": float(sig.confidence),
                        "timestamp": sig.timestamp if isinstance(sig.timestamp, str) else sig.timestamp.isoformat(),
                        "status": "active",
                    }
                    await broadcast_ict_signal(payload)
                    logger.info("Broadcast ICT signal: %s %s @ %.5f",
                                sig.symbol, payload["direction"], sig.entry_price)

        except Exception as exc:
            logger.warning("Background scanner error: %s", exc)

        await asyncio.sleep(interval_seconds)


def start_background_scanner(app, interval_seconds: int = 60) -> None:
    """Register the scanner loop as a FastAPI startup task."""
    import asyncio

    @app.on_event("startup")
    async def _start():
        asyncio.create_task(_background_scan_loop(interval_seconds))


def create_ict_router() -> tuple:
    """Return (http_router, ws_router) for mounting in the main app."""
    return router, ws_router
