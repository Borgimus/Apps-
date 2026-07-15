"""
FastAPI dashboard / REST API.

Endpoints:
  GET  /health                      — liveness check
  GET  /status                      — trading session status
  GET  /account                     — broker account summary
  GET  /positions                   — open positions
  GET  /orders                      — recent orders (optional ?status= filter)
  GET  /signals                     — recent signals from all strategies
  GET  /risk                        — current risk counters
  POST /kill-switch/activate        — set kill switch (halts all new orders)
  DELETE /kill-switch               — deactivate kill switch
  GET  /backtest/results            — list stored backtest summaries
  POST /backtest/run                — trigger a new backtest (async)
  GET  /strategies                  — list configured strategies and status
  WS   /ws/signals                  — real-time signal stream (WebSocket)

Security note: This API binds to 127.0.0.1 by default and has no
authentication.  Do not expose it to the public internet.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from .models import (
    AsyncSessionLocal,
    DBBacktestResult,
    DBOrder,
    DBPosition,
    DBRiskEvent,
    DBSignal,
    get_db,
    init_db,
)

logger = logging.getLogger(__name__)

# Shared broker instance — set by paper_trader or main.py at startup
_broker = None
_risk_manager = None
_paper_trader = None

# WebSocket connection manager
class _ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, message: dict):
        data = json.dumps(message)
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)


manager = _ConnectionManager()


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class StatusResponse(BaseModel):
    live_trading_enabled: bool
    broker: str
    kill_switch_active: bool
    session_date: Optional[str]
    trades_today: int
    daily_pnl: float


class BacktestRunRequest(BaseModel):
    strategy_id: str
    symbol: str
    start: Optional[str] = None
    end: Optional[str] = None
    interval: str = "1d"
    starting_equity: float = 100_000.0


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(
    broker=None,
    risk_manager=None,
    paper_trader=None,
) -> FastAPI:
    global _broker, _risk_manager, _paper_trader
    _broker = broker
    _risk_manager = risk_manager
    _paper_trader = paper_trader

    settings = get_settings()

    app = FastAPI(
        title="Options Trading Research System",
        description="Local dashboard API — NOT for production exposure",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount ICT strategy routers
    try:
        from .ict_api import create_ict_router, start_background_scanner
        ict_http_router, ict_ws_router = create_ict_router()
        app.include_router(ict_http_router)
        app.include_router(ict_ws_router)
        start_background_scanner(app, interval_seconds=60)
        logger.info("ICT strategy API mounted at /api/ict and /ws/ict/signals")
    except Exception as _exc:
        logger.warning("Failed to mount ICT router: %s", _exc)

    @app.on_event("startup")
    async def startup():
        await init_db()
        logger.info("Dashboard API started | live_trading=%s", settings.live_trading_enabled)
        if settings.live_trading_enabled:
            logger.warning("⚠️  LIVE TRADING IS ENABLED via API startup")

    # ── Health ─────────────────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

    # ── Status ─────────────────────────────────────────────────────────────

    @app.get("/status", response_model=StatusResponse)
    async def status():
        rm = _risk_manager
        return StatusResponse(
            live_trading_enabled=settings.live_trading_enabled,
            broker=settings.broker,
            kill_switch_active=settings.is_kill_switch_active(),
            session_date=str(rm._session_date) if rm and rm._session_date else None,
            trades_today=rm.trades_today if rm else 0,
            daily_pnl=float(rm.daily_pnl) if rm else 0.0,
        )

    # ── Account ────────────────────────────────────────────────────────────

    @app.get("/account")
    async def account():
        if _broker is None:
            raise HTTPException(503, "Broker not initialised")
        acct = await _broker.get_account()
        return {
            "account_id": acct.account_id,
            "equity": float(acct.equity),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "is_paper": acct.is_paper,
        }

    # ── Positions ──────────────────────────────────────────────────────────

    @app.get("/positions")
    async def positions(db: AsyncSession = Depends(get_db)):
        rows = (await db.execute(select(DBPosition))).scalars().all()
        return [
            {
                "symbol": r.symbol,
                "option_symbol": r.option_symbol,
                "quantity": r.quantity,
                "avg_cost": r.avg_cost,
                "current_price": r.current_price,
                "unrealized_pnl": r.unrealized_pnl,
                "strategy_id": r.strategy_id,
                "opened_at": str(r.opened_at),
            }
            for r in rows
        ]

    # ── Orders ─────────────────────────────────────────────────────────────

    @app.get("/orders")
    async def orders(
        status: Optional[str] = None,
        limit: int = 50,
        db: AsyncSession = Depends(get_db),
    ):
        query = select(DBOrder).order_by(DBOrder.created_at.desc()).limit(limit)
        if status:
            query = query.where(DBOrder.status == status)
        rows = (await db.execute(query)).scalars().all()
        return [
            {
                "order_id": r.order_id,
                "symbol": r.symbol,
                "option_symbol": r.option_symbol,
                "side": r.side,
                "quantity": r.quantity,
                "limit_price": r.limit_price,
                "filled_price": r.filled_price,
                "status": r.status,
                "is_paper": r.is_paper,
                "submitted_at": str(r.submitted_at),
            }
            for r in rows
        ]

    # ── Signals ────────────────────────────────────────────────────────────

    @app.get("/signals")
    async def signals(
        limit: int = 50,
        symbol: Optional[str] = None,
        db: AsyncSession = Depends(get_db),
    ):
        query = select(DBSignal).order_by(DBSignal.timestamp.desc()).limit(limit)
        if symbol:
            query = query.where(DBSignal.symbol == symbol)
        rows = (await db.execute(query)).scalars().all()
        return [
            {
                "id": r.id,
                "strategy_id": r.strategy_id,
                "symbol": r.symbol,
                "direction": r.direction,
                "timestamp": str(r.timestamp),
                "price": r.price,
                "confidence": r.confidence,
                "notes": r.notes,
            }
            for r in rows
        ]

    # ── Risk ───────────────────────────────────────────────────────────────

    @app.get("/risk")
    async def risk(db: AsyncSession = Depends(get_db)):
        rm = _risk_manager
        recent_events = (
            await db.execute(
                select(DBRiskEvent)
                .order_by(DBRiskEvent.timestamp.desc())
                .limit(20)
            )
        ).scalars().all()
        return {
            "trades_today": rm.trades_today if rm else 0,
            "daily_pnl": float(rm.daily_pnl) if rm else 0.0,
            "kill_switch_active": settings.is_kill_switch_active(),
            "max_trades_per_day": settings.risk.max_trades_per_day,
            "max_daily_loss_pct": settings.risk.max_daily_loss,
            "recent_events": [
                {
                    "event_type": e.event_type,
                    "check_name": e.check_name,
                    "message": e.message,
                    "timestamp": str(e.timestamp),
                }
                for e in recent_events
            ],
        }

    # ── Kill switch ────────────────────────────────────────────────────────

    @app.post("/kill-switch/activate")
    async def activate_kill_switch():
        Path(settings.kill_switch_file).touch()
        logger.warning("Kill switch ACTIVATED via API")
        return {"kill_switch": "active", "file": settings.kill_switch_file}

    @app.delete("/kill-switch")
    async def deactivate_kill_switch():
        p = Path(settings.kill_switch_file)
        if p.exists():
            p.unlink()
            logger.info("Kill switch deactivated via API")
            return {"kill_switch": "inactive"}
        return {"kill_switch": "was not active"}

    # ── Strategies ─────────────────────────────────────────────────────────

    @app.get("/strategies")
    async def strategies():
        cfg = settings
        strat_cfg = {
            "opening_range_breakout": cfg.risk,
            "vwap_reclaim": cfg.risk,
            "rsi_trend": cfg.risk,
            "ma_compression": cfg.risk,
        }
        return [
            {"id": "orb", "name": "Opening Range Breakout", "enabled": True},
            {"id": "vwap_reclaim", "name": "VWAP Reclaim/Rejection", "enabled": True},
            {"id": "rsi_trend", "name": "RSI + Trend Filter", "enabled": True},
            {"id": "ma_compression", "name": "MA Compression Breakout", "enabled": True},
        ]

    # ── Backtest results ───────────────────────────────────────────────────

    @app.get("/backtest/results")
    async def backtest_results(db: AsyncSession = Depends(get_db)):
        rows = (
            await db.execute(
                select(DBBacktestResult).order_by(DBBacktestResult.ran_at.desc()).limit(50)
            )
        ).scalars().all()
        return [
            {
                "id": r.id,
                "strategy_id": r.strategy_id,
                "symbol": r.symbol,
                "start_date": r.start_date,
                "end_date": r.end_date,
                "total_trades": r.total_trades,
                "win_rate": r.win_rate,
                "profit_factor": r.profit_factor,
                "total_pnl": r.total_pnl,
                "sharpe_ratio": r.sharpe_ratio,
                "max_drawdown": r.max_drawdown,
                "is_approximate": r.is_approximate,
                "ran_at": str(r.ran_at),
            }
            for r in rows
        ]

    @app.post("/backtest/run")
    async def run_backtest(req: BacktestRunRequest, db: AsyncSession = Depends(get_db)):
        from ..backtesting import BacktestEngine
        from ..strategies import (
            MACompressionStrategy,
            OpeningRangeBreakoutStrategy,
            RSITrendStrategy,
            VWAPReclaimStrategy,
        )

        strategy_map = {
            "orb": OpeningRangeBreakoutStrategy,
            "vwap_reclaim": VWAPReclaimStrategy,
            "rsi_trend": RSITrendStrategy,
            "ma_compression": MACompressionStrategy,
        }
        if req.strategy_id not in strategy_map:
            raise HTTPException(400, f"Unknown strategy: {req.strategy_id}")

        strat = strategy_map[req.strategy_id]()
        engine = BacktestEngine()
        result = await engine.run(
            strat, req.symbol, req.start, req.end, req.interval, req.starting_equity
        )
        report_path = engine.save_report(result)

        row = DBBacktestResult(
            strategy_id=result.strategy_id,
            symbol=result.symbol,
            start_date=result.start_date,
            end_date=result.end_date,
            total_trades=result.total_trades,
            win_rate=result.win_rate,
            profit_factor=result.profit_factor,
            total_pnl=result.total_pnl,
            max_drawdown=result.max_drawdown,
            sharpe_ratio=result.sharpe_ratio,
            expectancy=result.expectancy,
            is_approximate=result.is_approximate,
            report_path=report_path,
        )
        db.add(row)
        await db.commit()

        return result.to_dict()

    # ── WebSocket signal stream ────────────────────────────────────────────

    @app.websocket("/ws/signals")
    async def ws_signals(websocket: WebSocket):
        await manager.connect(websocket)
        try:
            while True:
                await websocket.receive_text()  # keep alive
        except WebSocketDisconnect:
            manager.disconnect(websocket)

    return app


async def broadcast_signal(signal_data: dict):
    """Call this from the paper trader to push signals to connected clients."""
    await manager.broadcast({"type": "signal", "data": signal_data})


async def broadcast_order(order_data: dict):
    await manager.broadcast({"type": "order", "data": order_data})
