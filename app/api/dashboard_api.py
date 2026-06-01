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
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from .models import (
    AsyncSessionLocal,
    DBBacktestResult,
    DBOrder,
    DBPendingOrder,
    DBPosition,
    DBRiskEvent,
    DBSessionLog,
    DBSignal,
    DBTradeJournal,
    get_db,
    init_db,
)

logger = logging.getLogger(__name__)

# Shared broker instance — set by paper_trader or main.py at startup
_broker = None
_risk_manager = None
_paper_trader = None
_position_manager = None   # app.trading.PositionManager — set externally
_fill_tracker = None       # app.trading.FillTracker — set externally
_scan_store: dict = {}     # latest scan results — updated by session runner

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
    trades_today: int          # backward-compat alias for entries_today
    entries_today: int
    pending_entries: int
    exits_today: int
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
    position_manager=None,
    fill_tracker=None,
    scan_results_store: Optional[dict] = None,
) -> FastAPI:
    global _broker, _risk_manager, _paper_trader, _position_manager, _fill_tracker, _scan_store
    _broker = broker
    _risk_manager = risk_manager
    _paper_trader = paper_trader
    _position_manager = position_manager
    _fill_tracker = fill_tracker
    if scan_results_store is not None:
        _scan_store = scan_results_store

    settings = get_settings()

    app = FastAPI(
        title="Options Trading Research System",
        description="Local dashboard API — NOT for production exposure",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],   # localhost-only by bind address; no auth token needed
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def startup():
        await init_db()
        logger.info("Dashboard API started | live_trading=%s", settings.live_trading_enabled)
        if settings.live_trading_enabled:
            logger.warning("⚠️  LIVE TRADING IS ENABLED via API startup")

    # ── Dashboard UI ───────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dashboard_ui():
        """Serve the single-page monitoring dashboard."""
        static_path = Path(__file__).parent.parent / "static" / "index.html"
        try:
            return HTMLResponse(content=static_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return HTMLResponse(
                content="<h1>Dashboard not built</h1><p>index.html not found.</p>",
                status_code=404,
            )

    # ── Health (comprehensive) ─────────────────────────────────────────────

    @app.get("/health")
    async def health(db: AsyncSession = Depends(get_db)):
        """
        Liveness and readiness check.

        Returns database connectivity, runner heartbeat age, current market
        session status, and paper-mode confirmation.
        """
        from datetime import date as _date, timezone, time as _time
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo("America/New_York")
        now = datetime.now(tz=_ET)

        # ── Database ───────────────────────────────────────────────────────
        db_ok = False
        try:
            await db.execute(text("SELECT 1"))
            db_ok = True
        except Exception as exc:
            logger.warning("Health check: DB error: %s", exc)

        # ── Runner heartbeat ───────────────────────────────────────────────
        runner_active = False
        heartbeat_age_secs: Optional[int] = None
        try:
            row = (
                await db.execute(
                    select(DBSessionLog)
                    .where(DBSessionLog.session_date == str(_date.today()))
                    .where(DBSessionLog.event == "heartbeat")
                    .order_by(DBSessionLog.timestamp.desc())
                    .limit(1)
                )
            ).scalars().first()
            if row and row.timestamp:
                ts = row.timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=_ET)
                age = (now - ts.astimezone(_ET)).total_seconds()
                heartbeat_age_secs = int(age)
                runner_active = age < 600
        except Exception:
            pass

        # ── Market session status ──────────────────────────────────────────
        weekday = now.weekday()
        if weekday >= 5:
            mkt_status = "weekend"
        elif now.time() < _time(9, 30):
            mkt_status = "pre_market"
        elif now.time() < _time(16, 0):
            mkt_status = "open"
        else:
            mkt_status = "after_hours"

        # ── Data feed freshness (last session log entry) ────────────────
        feed_age_secs: Optional[int] = None
        try:
            last_log = (
                await db.execute(
                    select(DBSessionLog)
                    .where(DBSessionLog.session_date == str(_date.today()))
                    .order_by(DBSessionLog.timestamp.desc())
                    .limit(1)
                )
            ).scalars().first()
            if last_log and last_log.timestamp:
                ts = last_log.timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=_ET)
                feed_age_secs = int((now - ts.astimezone(_ET)).total_seconds())
        except Exception:
            pass

        overall = "ok" if db_ok else "degraded"

        return {
            "status": overall,
            "paper_mode": not settings.live_trading_enabled,
            "paper_evaluation_mode": settings.paper_evaluation_mode,
            "kill_switch_active": settings.is_kill_switch_active(),
            "database": {
                "status": "ok" if db_ok else "error",
            },
            "runner": {
                "active": runner_active,
                "heartbeat_age_secs": heartbeat_age_secs,
            },
            "data_feed": {
                "last_event_age_secs": feed_age_secs,
                "stale": feed_age_secs is not None and feed_age_secs > 600,
            },
            "market": {
                "status": mkt_status,
                "time_et": now.strftime("%H:%M:%S"),
                "date": str(_date.today()),
            },
            "timestamp": now.isoformat(),
        }

    # ── Pending orders (from DBPendingOrder) ───────────────────────────────

    @app.get("/pending-orders")
    async def pending_orders(
        session_date: Optional[str] = None,
        db: AsyncSession = Depends(get_db),
    ):
        """Return open pending orders for today (or a given session date)."""
        from datetime import date as _date
        target = session_date or str(_date.today())
        rows = (
            await db.execute(
                select(DBPendingOrder)
                .where(DBPendingOrder.session_date == target)
                .order_by(DBPendingOrder.submitted_at.desc())
            )
        ).scalars().all()
        return [
            {
                "order_id": r.order_id,
                "option_symbol": r.option_symbol,
                "symbol": r.symbol,
                "strategy_id": r.strategy_id,
                "direction": r.direction,
                "quantity": r.quantity,
                "limit_price": r.limit_price,
                "status": r.status,
                "filled_quantity": r.filled_quantity,
                "avg_fill_price": r.avg_fill_price,
                "submitted_at": str(r.submitted_at) if r.submitted_at else None,
                "last_polled_at": str(r.last_polled_at) if r.last_polled_at else None,
            }
            for r in rows
        ]

    # ── Status ─────────────────────────────────────────────────────────────

    @app.get("/status", response_model=StatusResponse)
    async def status():
        rm = _risk_manager
        entries = rm.entries_today if rm else 0
        return StatusResponse(
            live_trading_enabled=settings.live_trading_enabled,
            broker=settings.broker,
            kill_switch_active=settings.is_kill_switch_active(),
            session_date=str(rm._session_date) if rm and rm._session_date else None,
            trades_today=entries,
            entries_today=entries,
            pending_entries=rm.pending_entries if rm else 0,
            exits_today=rm.exits_today if rm else 0,
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
        entries = rm.entries_today if rm else 0
        pending = rm.pending_entries if rm else 0
        return {
            "trades_today": entries,
            "entries_today": entries,
            "pending_entries": pending,
            "exits_today": rm.exits_today if rm else 0,
            "trades_remaining": max(0, settings.risk.max_trades_per_day - (entries + pending)),
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

    # ── Open positions (live PositionManager) ──────────────────────────────

    @app.get("/positions/open")
    async def open_positions():
        pm = _position_manager
        if pm is None:
            return []
        return pm.to_dict_list()

    # ── Session supervision state ──────────────────────────────────────────

    @app.get("/session/state")
    async def session_state(db: AsyncSession = Depends(get_db)):
        """
        Full snapshot of the current trading session for supervised monitoring.

        Returns open positions with computed stop/profit levels, pending-order
        count, risk utilisation, and session timing.  Safe to poll at any
        frequency — all reads are non-blocking.
        """
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        _ET = _ZI("America/New_York")
        now = _dt.now(tz=_ET)

        rm = _risk_manager
        pm = _position_manager
        ft = _fill_tracker

        open_pos = pm.to_dict_list() if pm else []
        unrealized_total = sum(p.get("unrealized_pnl", 0.0) for p in open_pos)

        # Pending orders — prefer live FillTracker count, fall back to DB
        if ft is not None:
            pending_count = ft.count()
        else:
            try:
                today_str = now.strftime("%Y-%m-%d")
                result = await db.execute(
                    select(func.count(DBPendingOrder.id))
                    .where(DBPendingOrder.session_date == today_str)
                    .where(DBPendingOrder.status == "pending")
                )
                pending_count = result.scalar() or 0
            except Exception:
                pending_count = 0

        daily_pnl = float(rm.daily_pnl) if rm else 0.0
        entries_today = rm.entries_today if rm else 0
        pending_entries = rm.pending_entries if rm else 0
        exits_today = rm.exits_today if rm else 0
        max_trades = settings.risk.max_trades_per_day

        _uni = settings.universe
        _max_active_pos = getattr(_uni, "max_active_positions", 1)
        _max_sym_day = getattr(_uni, "max_symbols_traded_per_day", 1)
        _max_contracts = getattr(_uni, "max_contracts_per_position", 1)

        return {
            "session_date": str(rm._session_date) if rm and rm._session_date else now.strftime("%Y-%m-%d"),
            "now_et": now.strftime("%H:%M:%S"),
            "paper_mode": not settings.live_trading_enabled,
            "paper_evaluation_mode": settings.paper_evaluation_mode,
            "kill_switch_active": settings.is_kill_switch_active(),
            # ── Trade counts ───────────────────────────────────────────────
            "trades_today": entries_today,       # backward-compat alias
            "entries_today": entries_today,
            "pending_entries": pending_entries,
            "exits_today": exits_today,
            "round_trips_completed": min(entries_today, exits_today),
            "orders_submitted": entries_today + pending_entries,
            "max_trades_per_day": max_trades,
            "trades_remaining": max(0, max_trades - (entries_today + pending_entries)),
            # ── PnL ────────────────────────────────────────────────────────
            "daily_pnl": daily_pnl,
            "unrealized_pnl_total": round(unrealized_total, 2),
            "total_pnl": round(daily_pnl + unrealized_total, 2),
            # ── Position limits ────────────────────────────────────────────
            "open_positions_count": len(open_pos),
            "max_active_positions": _max_active_pos,
            "open_positions": open_pos,
            "pending_orders_count": pending_count,
            "max_contracts_per_position": _max_contracts,
            # ── Symbol limits ──────────────────────────────────────────────
            "active_symbols": _scan_store.get("active_symbols", []),
            "max_symbols_traded_per_day": _max_sym_day,
            # ── Scanner state ──────────────────────────────────────────────
            "scanner_standby": _scan_store.get("standby", False),
            "standby_reason": _scan_store.get("standby_reason", None),
            "enabled_groups": _scan_store.get("enabled_groups", []),
            # ── Strategy configuration and readiness ───────────────────────
            "strategy_configs": _scan_store.get("strategy_configs", []),
            "strategy_readiness": _scan_store.get("strategy_readiness", []),
        }

    # ── Trade journal ──────────────────────────────────────────────────────

    @app.get("/journal")
    async def journal(
        limit: int = 100,
        status: Optional[str] = None,
        strategy_id: Optional[str] = None,
        symbol: Optional[str] = None,
        db: AsyncSession = Depends(get_db),
    ):
        query = (
            select(DBTradeJournal)
            .order_by(DBTradeJournal.created_at.desc())
            .limit(limit)
        )
        if status:
            query = query.where(DBTradeJournal.status == status)
        if strategy_id:
            query = query.where(DBTradeJournal.strategy_id == strategy_id)
        if symbol:
            query = query.where(DBTradeJournal.underlying_symbol == symbol)
        rows = (await db.execute(query)).scalars().all()
        return [
            {
                "id": r.id,
                "status": r.status,
                "entry_time": str(r.entry_time) if r.entry_time else None,
                "exit_time": str(r.exit_time) if r.exit_time else None,
                "strategy_id": r.strategy_id,
                "signal_direction": r.signal_direction,
                "underlying_symbol": r.underlying_symbol,
                "underlying_price": r.underlying_price,
                "option_symbol": r.option_symbol,
                "expiration": r.expiration,
                "strike": r.strike,
                "option_type": r.option_type,
                "delta": r.delta,
                "iv": r.iv,
                "spread_pct": r.spread_pct,
                "limit_price": r.limit_price,
                "fill_price": r.fill_price,
                "exit_price": r.exit_price,
                "exit_reason": r.exit_reason,
                "realized_pnl": r.realized_pnl,
                "unrealized_pnl": r.unrealized_pnl,
                "slippage": r.slippage,
                "hold_duration_secs": r.hold_duration_secs,
                "rejection_reason": r.rejection_reason,
                "bid": r.bid,
                "ask": r.ask,
                "quantity": r.quantity,
                "filled_quantity": r.filled_quantity,
                "order_id": r.order_id,
                "weekday": r.weekday,
                "is_paper": r.is_paper,
                "notes": r.notes,
            }
            for r in rows
        ]

    # ── Analytics ──────────────────────────────────────────────────────────

    @app.get("/analytics")
    async def analytics_summary(db: AsyncSession = Depends(get_db)):
        from ..analytics import AnalyticsEngine
        engine = AnalyticsEngine(db)
        return await engine.summary()

    @app.get("/analytics/strategies")
    async def analytics_by_strategy(db: AsyncSession = Depends(get_db)):
        from ..analytics import AnalyticsEngine
        engine = AnalyticsEngine(db)
        return await engine.by_strategy()

    @app.get("/analytics/hourly")
    async def analytics_by_hour(db: AsyncSession = Depends(get_db)):
        from ..analytics import AnalyticsEngine
        engine = AnalyticsEngine(db)
        return await engine.by_hour()

    @app.get("/analytics/delta")
    async def analytics_by_delta(db: AsyncSession = Depends(get_db)):
        from ..analytics import AnalyticsEngine
        engine = AnalyticsEngine(db)
        return await engine.by_delta_range()

    @app.get("/analytics/iv")
    async def analytics_by_iv(db: AsyncSession = Depends(get_db)):
        from ..analytics import AnalyticsEngine
        engine = AnalyticsEngine(db)
        return await engine.by_iv_percentile()

    @app.get("/analytics/spread")
    async def analytics_spread(db: AsyncSession = Depends(get_db)):
        from ..analytics import AnalyticsEngine
        engine = AnalyticsEngine(db)
        return await engine.spread_analysis()

    @app.get("/analytics/rejections")
    async def analytics_rejections(db: AsyncSession = Depends(get_db)):
        from ..analytics import AnalyticsEngine
        engine = AnalyticsEngine(db)
        return await engine.rejection_summary()

    @app.get("/analytics/weekday")
    async def analytics_by_weekday(db: AsyncSession = Depends(get_db)):
        from ..analytics import AnalyticsEngine
        engine = AnalyticsEngine(db)
        return await engine.by_weekday()

    @app.get("/analytics/equity-curve")
    async def analytics_equity_curve(
        starting_equity: float = 0.0,
        db: AsyncSession = Depends(get_db),
    ):
        from ..analytics import AnalyticsEngine
        engine = AnalyticsEngine(db)
        return await engine.equity_curve(starting_equity)

    # ── Journal enriched fields ────────────────────────────────────────────

    @app.get("/journal/daily")
    async def journal_daily(
        session_date: Optional[str] = None,
        db: AsyncSession = Depends(get_db),
    ):
        """Return all journal entries for a specific session date (YYYY-MM-DD)."""
        from datetime import date as _date
        target = session_date or str(_date.today())
        query = (
            select(DBTradeJournal)
            .where(DBTradeJournal.session_date == target)
            .order_by(DBTradeJournal.entry_time)
        )
        rows = (await db.execute(query)).scalars().all()
        return {
            "session_date": target,
            "total": len(rows),
            "closed": sum(1 for r in rows if r.status == "closed"),
            "rejected": sum(1 for r in rows if r.status == "rejected"),
            "open": sum(1 for r in rows if r.status == "open"),
            "realized_pnl": round(
                sum(r.realized_pnl for r in rows if r.realized_pnl is not None), 2
            ),
            "trades": [
                {
                    "id": r.id,
                    "status": r.status,
                    "entry_time": str(r.entry_time) if r.entry_time else None,
                    "exit_time": str(r.exit_time) if r.exit_time else None,
                    "strategy_id": r.strategy_id,
                    "signal_direction": r.signal_direction,
                    "option_symbol": r.option_symbol,
                    "delta": r.delta,
                    "iv": r.iv,
                    "bid": r.bid,
                    "ask": r.ask,
                    "spread_pct": r.spread_pct,
                    "limit_price": r.limit_price,
                    "fill_price": r.fill_price,
                    "exit_price": r.exit_price,
                    "exit_reason": r.exit_reason,
                    "realized_pnl": r.realized_pnl,
                    "slippage": r.slippage,
                    "hold_duration_secs": r.hold_duration_secs,
                    "rejection_reason": r.rejection_reason,
                }
                for r in rows
            ],
        }

    # ── Session logs ───────────────────────────────────────────────────────

    @app.get("/session/logs")
    async def session_logs(
        session_date: Optional[str] = None,
        event: Optional[str] = None,
        limit: int = 200,
        db: AsyncSession = Depends(get_db),
    ):
        from datetime import date as _date
        target = session_date or str(_date.today())
        query = (
            select(DBSessionLog)
            .where(DBSessionLog.session_date == target)
            .order_by(DBSessionLog.timestamp.desc())
            .limit(limit)
        )
        if event:
            query = query.where(DBSessionLog.event == event)
        rows = (await db.execute(query)).scalars().all()
        return [
            {
                "id": r.id,
                "timestamp": str(r.timestamp),
                "level": r.level,
                "event": r.event,
                "symbol": r.symbol,
                "message": r.message,
                "data": r.data_json,
            }
            for r in rows
        ]

    # ── Heartbeat ─────────────────────────────────────────────────────────

    @app.get("/heartbeat")
    async def heartbeat(db: AsyncSession = Depends(get_db)):
        """
        Latest heartbeat from the session runner.
        Returns the most recent heartbeat log entry and elapsed seconds since it.
        """
        from datetime import date as _date, timezone
        today = str(_date.today())
        row = (
            await db.execute(
                select(DBSessionLog)
                .where(DBSessionLog.session_date == today)
                .where(DBSessionLog.event == "heartbeat")
                .order_by(DBSessionLog.timestamp.desc())
                .limit(1)
            )
        ).scalars().first()

        if row is None:
            return {"runner_active": False, "last_heartbeat": None, "stale_secs": None}

        now_utc = datetime.now(tz=timezone.utc)
        ts = row.timestamp
        if ts.tzinfo is None:
            from zoneinfo import ZoneInfo
            ts = ts.replace(tzinfo=ZoneInfo("America/New_York"))
        stale = (now_utc - ts.astimezone(timezone.utc)).total_seconds()
        return {
            "runner_active": stale < 600,   # stale after 10 min
            "last_heartbeat": str(row.timestamp),
            "stale_secs": round(stale),
            "message": row.message,
        }

    # ── Scan results ───────────────────────────────────────────────────────

    @app.get("/scan/results")
    async def scan_results(db: AsyncSession = Depends(get_db)):
        """
        Latest scan pipeline results.

        Returns in-memory scan store (set by session runner each scan cycle)
        augmented with the most recent DB scan rows for today.
        """
        from datetime import date as _date
        import json as _json
        today = str(_date.today())

        # Pull today's DB records as fallback / supplement
        try:
            from .models import DBScanResult
            rows = (
                await db.execute(
                    select(DBScanResult)
                    .where(DBScanResult.session_date == today)
                    .order_by(DBScanResult.scanned_at.desc())
                )
            ).scalars().all()
            db_results = [
                {
                    "symbol": r.symbol,
                    "score": r.score,
                    "signal_type": r.signal_type,
                    "is_rejected": r.is_rejected,
                    "selected": r.selected,
                    "reason_codes": _json.loads(r.reason_codes or "[]"),
                    "rejected_reasons": _json.loads(r.rejected_reasons or "[]"),
                    "rvol": r.rvol,
                    "rsi": r.rsi,
                    "atr_pct": r.atr_pct,
                    "trend": r.trend,
                    "price_vs_vwap": r.price_vs_vwap,
                    "gap_pct": r.gap_pct,
                    "universe_group": r.universe_group,
                    "scanned_at": str(r.scanned_at),
                }
                for r in rows
            ]

            # Group-level breakdown (candidates and rejections by group)
            by_group: Dict[str, Dict] = {}
            for r in rows:
                grp = r.universe_group or "unknown"
                if grp not in by_group:
                    by_group[grp] = {"candidates": 0, "rejected": 0, "selected": 0}
                by_group[grp]["candidates"] += 1
                if r.is_rejected:
                    by_group[grp]["rejected"] += 1
                if r.selected:
                    by_group[grp]["selected"] += 1
        except Exception:
            db_results = []
            by_group = {}

        enabled_groups = _scan_store.get("enabled_groups", [])
        return {
            "session_date": today,
            "enabled_groups": enabled_groups,
            "by_group": by_group,
            "live": dict(_scan_store),
            "db_results": db_results,
            "count": len(db_results),
        }

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


# ── Standalone app instance ────────────────────────────────────────────────────
# Used by uvicorn when running the dashboard without the trading loop:
#   uvicorn app.api.dashboard_api:app --reload
# Broker-dependent endpoints (/account, /positions) return 503 in this mode.
app = create_app()
