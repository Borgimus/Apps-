"""
Paper Trading Engine.

Orchestrates the full intraday loop:
  1. Fetch market data (yfinance for research; broker quotes for execution).
  2. Run all enabled strategies to generate signals.
  3. Apply IV crush filter and liquidity filter.
  4. Run risk checks on each signal.
  5. Select the best option contract.
  6. Submit limit orders to the broker (paper or live, per config).
  7. Monitor open positions and apply exit logic.
  8. Persist all events to the database.
  9. Broadcast signals/orders to the dashboard WebSocket.

Run this module directly for paper trading:
  python paper_trader.py

Or import PaperTrader and call .run() from main.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal as os_signal
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import uvicorn

from app.api.dashboard_api import broadcast_order, broadcast_signal, create_app
from app.api.models import (
    AsyncSessionLocal,
    DBOrder,
    DBPosition,
    DBRiskEvent,
    DBSignal,
    init_db,
)
from app.brokers import get_broker
from app.brokers.broker_interface import (
    OrderRequest,
    OrderSide,
    OrderType,
)
from app.config import get_settings
from app.data import YFinanceDataSource
from app.risk import RiskManager
from app.strategies import (
    IVCrushFilter,
    LiquidityFilter,
    MACompressionStrategy,
    OpeningRangeBreakoutStrategy,
    RSITrendStrategy,
    VWAPReclaimStrategy,
)
from app.strategies.strategy_base import Signal, SignalDirection

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


class PaperTrader:
    """
    Main orchestrator for paper (and optionally live) trading.
    """

    def __init__(self):
        self._settings = get_settings()
        self._broker = get_broker(self._settings)
        self._data = YFinanceDataSource()
        self._risk = RiskManager(self._settings)
        self._iv_filter = IVCrushFilter(
            {
                "earnings_blackout_days": self._settings.risk.earnings_blackout_days,
                "max_iv_rank_pre_earnings": 60,
                "allow_earnings_trades": self._settings.risk.allow_earnings_trades,
            }
        )
        self._liquidity_filter = LiquidityFilter(
            {
                "min_open_interest": self._settings.risk.min_open_interest,
                "min_volume": self._settings.risk.min_volume,
                "max_spread_pct": self._settings.risk.max_spread_pct,
                "delta_target_min": self._settings.options.delta_target_min,
                "delta_target_max": self._settings.options.delta_target_max,
            }
        )
        self._strategies = [
            OpeningRangeBreakoutStrategy(),
            VWAPReclaimStrategy(),
            RSITrendStrategy(),
            MACompressionStrategy(),
        ]
        self._shutdown = False
        self._open_positions: Dict[str, dict] = {}

        if not self._settings.live_trading_enabled:
            logger.info("PaperTrader: running in PAPER mode (no live orders)")
        else:
            logger.warning(
                "⚠️  PaperTrader: LIVE TRADING ENABLED — real orders will be submitted"
            )

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self):
        await init_db()
        acct = await self._broker.get_account()
        self._risk.start_session(acct.equity)
        logger.info(
            "Session started | equity=%.2f | paper=%s", acct.equity, acct.is_paper
        )

        # Register signal handlers for graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (os_signal.SIGINT, os_signal.SIGTERM):
            loop.add_signal_handler(sig, self._request_shutdown)

        try:
            while not self._shutdown:
                await self._trading_cycle()
                # Wait before next cycle (5 min polling interval)
                await asyncio.sleep(300)
        finally:
            logger.info("PaperTrader shutting down gracefully")

    def _request_shutdown(self):
        logger.info("Shutdown signal received")
        self._shutdown = True

    async def _trading_cycle(self):
        """Single pass of the trading loop."""
        now = datetime.now(tz=ET)

        # Check kill switch
        if self._settings.is_kill_switch_active():
            logger.warning("Kill switch is active — skipping trading cycle")
            return

        # Check session hours
        open_h, open_m = map(int, self._settings.market_open.split(":"))
        close_h, close_m = map(int, self._settings.market_close.split(":"))
        market_open = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
        market_close = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)

        if not (market_open <= now <= market_close):
            logger.debug("Outside market hours — skipping cycle")
            return

        for symbol in self._settings.watchlist:
            try:
                await self._process_symbol(symbol, now)
            except Exception as exc:
                logger.error("Error processing %s: %s", symbol, exc, exc_info=True)

    async def _process_symbol(self, symbol: str, now: datetime):
        # Fetch intraday bars for signal generation
        bars = await self._data.get_intraday_bars(symbol, interval="5m", days_back=3)
        if bars.empty:
            return

        all_signals: List[Signal] = []
        for strategy in self._strategies:
            if symbol not in strategy.symbols and strategy.symbols:
                continue
            signals = strategy.generate_signals(bars, symbol)
            all_signals.extend(signals)

        if not all_signals:
            return

        # Apply IV crush filter
        filtered = self._iv_filter.apply(all_signals)

        # Process each signal
        for sig in filtered:
            if not sig.is_actionable():
                continue
            await self._handle_signal(sig, now)

    async def _handle_signal(self, sig: Signal, now: datetime):
        logger.info(
            "Signal: %s %s %s @ %.2f | confidence=%.2f",
            sig.strategy_id,
            sig.symbol,
            sig.direction.value,
            sig.price,
            sig.confidence,
        )

        # Persist signal
        await self._save_signal(sig)
        await broadcast_signal(
            {
                "strategy": sig.strategy_id,
                "symbol": sig.symbol,
                "direction": sig.direction.value,
                "price": sig.price,
                "timestamp": sig.timestamp.isoformat(),
                "notes": sig.notes,
            }
        )

        # Get account equity
        try:
            acct = await self._broker.get_account()
        except Exception as e:
            logger.error("Cannot get account for risk check: %s", e)
            return

        # Choose option expiration
        target_dte = None
        try:
            expirations = await self._broker.get_available_expirations(sig.symbol)
            today = now.date()
            for dte in self._settings.options.preferred_dte:
                target_exp = today + timedelta(days=dte)
                if target_exp in expirations:
                    target_dte = target_exp
                    break
            if not target_dte and expirations:
                target_dte = min(expirations, key=lambda d: abs((d - today).days))
        except NotImplementedError:
            logger.warning("Broker does not support get_available_expirations — skipping")
            return

        if not target_dte:
            logger.warning("No suitable expiration found for %s", sig.symbol)
            return

        # Fetch option chain from broker (NOT yfinance — execution data)
        try:
            chain = await self._broker.get_option_chain(sig.symbol, target_dte)
        except Exception as e:
            logger.error("Failed to fetch option chain for %s: %s", sig.symbol, e)
            return

        # Select contract via liquidity filter
        contract = self._liquidity_filter.select_contract(chain, sig)
        if not contract:
            logger.info("No liquid contract found for %s %s", sig.symbol, sig.direction)
            await self._save_risk_event("no_liquid_contract", sig.symbol, None, None,
                                        "Liquidity filter: no qualifying contract")
            return

        # Build order request
        side = (
            OrderSide.BUY_TO_OPEN
            if sig.direction in (SignalDirection.LONG, SignalDirection.SHORT)
            else OrderSide.SELL_TO_CLOSE
        )
        offset = Decimal(str(self._settings.options.limit_price_offset_pct))
        limit_price = contract.ask * (1 + offset)

        request = OrderRequest(
            symbol=sig.symbol,
            option_symbol=contract.option_symbol,
            side=side,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=limit_price,
            strategy_id=sig.strategy_id,
            notes=sig.notes,
        )

        # Risk check
        risk_result = self._risk.check_order(
            request=request,
            equity=acct.equity,
            contract=contract,
            now=now,
        )

        if not risk_result.passed:
            logger.warning("Risk check FAILED: %s", risk_result.summary())
            for check, msg in zip(risk_result.failed_checks, risk_result.messages):
                await self._save_risk_event("order_rejected", sig.symbol, contract.option_symbol,
                                            check.value, msg)
            return

        # Adjust quantity
        request.quantity = risk_result.approved_quantity

        # Submit order
        try:
            order_result = await self._broker.place_option_order(request)
            self._risk.record_trade()
            logger.info(
                "Order submitted: %s | %s | qty=%d | price=%.2f",
                order_result.order_id,
                contract.option_symbol,
                request.quantity,
                limit_price,
            )
            await self._save_order(order_result, sig.strategy_id)
            await broadcast_order(
                {
                    "order_id": order_result.order_id,
                    "symbol": sig.symbol,
                    "option_symbol": contract.option_symbol,
                    "side": side.value,
                    "quantity": request.quantity,
                    "limit_price": float(limit_price),
                    "status": order_result.status.value,
                }
            )
        except Exception as e:
            logger.error("Order submission failed: %s", e, exc_info=True)

    # ── Database helpers ──────────────────────────────────────────────────────

    async def _save_signal(self, sig: Signal):
        async with AsyncSessionLocal() as db:
            row = DBSignal(
                strategy_id=sig.strategy_id,
                symbol=sig.symbol,
                direction=sig.direction.value,
                timestamp=sig.timestamp,
                price=sig.price,
                confidence=sig.confidence,
                notes=sig.notes,
                metadata_json=json.dumps(sig.metadata),
            )
            db.add(row)
            await db.commit()

    async def _save_order(self, order_result, strategy_id: str):
        async with AsyncSessionLocal() as db:
            row = DBOrder(
                order_id=order_result.order_id,
                strategy_id=strategy_id,
                symbol=order_result.symbol,
                option_symbol=order_result.option_symbol,
                side=order_result.side.value,
                quantity=order_result.quantity,
                limit_price=float(order_result.limit_price),
                filled_price=float(order_result.filled_price) if order_result.filled_price else None,
                filled_quantity=order_result.filled_quantity,
                status=order_result.status.value,
                is_paper=not self._settings.live_trading_enabled,
                submitted_at=order_result.submitted_at,
                filled_at=order_result.filled_at,
            )
            db.add(row)
            await db.commit()

    async def _save_risk_event(
        self,
        event_type: str,
        symbol: Optional[str],
        option_symbol: Optional[str],
        check_name: Optional[str],
        message: str,
    ):
        async with AsyncSessionLocal() as db:
            row = DBRiskEvent(
                event_type=event_type,
                symbol=symbol,
                option_symbol=option_symbol,
                check_name=check_name,
                message=message,
            )
            db.add(row)
            await db.commit()


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    trader = PaperTrader()

    settings = get_settings()
    app = create_app(
        broker=trader._broker,
        risk_manager=trader._risk,
        paper_trader=trader,
    )

    config = uvicorn.Config(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )
    server = uvicorn.Server(config)

    # Run both the trading loop and the API server concurrently
    await asyncio.gather(
        trader.run(),
        server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
