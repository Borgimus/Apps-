"""
Daily Session Runner — continuous intraday paper-trading loop.

Runs from market open until market close, polling every POLL_INTERVAL seconds.
Each poll cycle:
  1. Heartbeat check (connectivity + account)
  2. Stale-quote detection
  3. Update open positions (fetch quotes, evaluate exit conditions)
  4. Scan for new signals and attempt order placement
  5. Log structured session event

Safety guarantees:
  • LIVE_TRADING_ENABLED=true aborts immediately.
  • Kill switch file respected on every cycle.
  • All positions force-closed at EOD_EXIT_TIME.
  • SIGTERM / SIGINT trigger graceful shutdown (close positions, then exit).
  • API failures are retried with exponential backoff; after MAX_RETRIES the
    cycle is skipped and an error is logged — the runner never crashes.

Usage:
  python scripts/session_runner.py                # runs today's session
  python scripts/session_runner.py --dry-run      # no orders placed
  python scripts/session_runner.py --symbol QQQ SPY
  python scripts/session_runner.py --poll 60      # poll every 60 s
  python scripts/session_runner.py --log-level INFO
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
import warnings
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent.parent))

ET = ZoneInfo("America/New_York")
logger = logging.getLogger("session_runner")

# ── Globals (set in main, read in signal handler) ────────────────────────────
_shutdown_requested = False


def _request_shutdown(signum, frame):
    global _shutdown_requested
    logger.warning("Shutdown signal received (%s) — finishing current cycle then exiting", signum)
    _shutdown_requested = True


# ── Retry helper ─────────────────────────────────────────────────────────────

async def _retry(coro_fn, label: str = "", max_retries: int = 3):
    delay = 2
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            return await coro_fn()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                logger.warning("[%s] attempt %d failed: %s — retrying in %ds", label, attempt, exc, delay)
                await asyncio.sleep(delay)
                delay *= 2
    logger.error("[%s] all %d attempts failed: %s", label, max_retries, last_exc)
    raise last_exc


# ── Stale quote detection ─────────────────────────────────────────────────────

def _is_stale(fetched_at: Optional[datetime], max_age_secs: int = 60) -> bool:
    if fetched_at is None:
        return True
    age = (datetime.now(tz=ET) - fetched_at.astimezone(ET)).total_seconds()
    return age > max_age_secs


# ── Position monitor (update + exit) ─────────────────────────────────────────

async def monitor_positions(
    broker,
    pm,
    journal,
    risk,
    now: datetime,
    dry_run: bool,
) -> int:
    """
    For every open position, fetch a live quote, check exit conditions,
    and close if triggered.  Returns number of positions closed.
    """
    closed = 0
    for pos in list(pm.open_positions()):
        # Fetch live quote with retry
        try:
            quote = await _retry(
                lambda p=pos: broker.get_option_quote(p.option_symbol),
                label=f"get_option_quote({pos.option_symbol})",
            )
            current_price = float(quote.mid) if float(quote.mid) > 0 else pos.entry_price
        except Exception:
            current_price = pos.entry_price  # hold without data

        pm.update_price(pos.option_symbol, current_price)
        reason = pm.should_exit(pos.option_symbol, current_price, now)

        if reason:
            pnl = (current_price - pos.entry_price) * 100 * pos.quantity
            hold_secs = (now - pos.entry_time).total_seconds()
            logger.info(
                "Position exit | %s | reason=%s | price=%.4f | pnl=%.2f",
                pos.option_symbol, reason, current_price, pnl,
            )
            if not dry_run:
                pm_pos = pm.close(pos.option_symbol, current_price, pnl)
                risk.record_trade(Decimal(str(pnl)))
                if pos.journal_id and journal:
                    await journal.record_exit(
                        journal_id=pos.journal_id,
                        exit_time=now,
                        exit_price=current_price,
                        exit_reason=reason,
                        realized_pnl=pnl,
                        hold_duration_secs=hold_secs,
                    )
                    await journal.log_event(
                        event="exit",
                        message=f"{pos.option_symbol} closed: {reason} pnl={pnl:.2f}",
                        level="info",
                        symbol=pos.symbol,
                        data={"reason": reason, "pnl": round(pnl, 2), "hold_secs": round(hold_secs)},
                    )
                    await journal.commit()
            else:
                logger.info("DRY RUN: would close %s (%s)", pos.option_symbol, reason)
                pm.close(pos.option_symbol, current_price, pnl)
            closed += 1
    return closed


# ── EOD force-close all positions ────────────────────────────────────────────

async def eod_liquidate(broker, pm, journal, risk, now: datetime, dry_run: bool):
    """Force-close every open position at end-of-day."""
    positions = pm.open_positions()
    if not positions:
        return
    logger.warning("EOD liquidation: closing %d position(s)", len(positions))
    for pos in list(positions):
        try:
            quote = await _retry(
                lambda p=pos: broker.get_option_quote(p.option_symbol),
                label=f"eod_quote({pos.option_symbol})",
            )
            exit_price = float(quote.mid) if float(quote.mid) > 0 else pos.entry_price
        except Exception:
            exit_price = pos.entry_price

        pnl = (exit_price - pos.entry_price) * 100 * pos.quantity
        hold_secs = (now - pos.entry_time).total_seconds()

        if not dry_run:
            pm.close(pos.option_symbol, exit_price, pnl)
            risk.record_trade(Decimal(str(pnl)))
            if pos.journal_id and journal:
                await journal.record_exit(
                    journal_id=pos.journal_id,
                    exit_time=now,
                    exit_price=exit_price,
                    exit_reason="eod_exit",
                    realized_pnl=pnl,
                    hold_duration_secs=hold_secs,
                )
                await journal.commit()
        else:
            pm.close(pos.option_symbol, exit_price, pnl)
        logger.info("EOD closed %s pnl=%.2f", pos.option_symbol, pnl)


# ── Signal scan + order placement (one poll cycle) ───────────────────────────

async def scan_and_place(
    symbol: str,
    broker,
    data,
    strategies,
    iv_filter,
    liq_filter,
    risk,
    pm,
    journal,
    settings,
    now: datetime,
    dry_run: bool,
    fill_tracker=None,
) -> int:
    """Return number of orders placed (or would-be placed in dry-run)."""
    from app.brokers.broker_interface import OrderRequest, OrderSide, OrderType
    from app.strategies.strategy_base import SignalDirection

    # Fetch bars
    try:
        bars = await _retry(
            lambda: data.get_intraday_bars(symbol, interval="5m", days_back=3),
            label=f"bars({symbol})",
        )
    except Exception as exc:
        logger.error("Cannot fetch bars for %s: %s", symbol, exc)
        return 0

    if bars.empty:
        return 0

    # Generate signals
    all_signals = []
    for strat in strategies:
        sigs = strat.generate_signals(bars, symbol)
        all_signals.extend(sigs)

    # IV filter
    filtered = iv_filter.apply(all_signals)
    actionable = [s for s in filtered if s.is_actionable()]

    placed = 0
    for sig in actionable:
        # Only act on signals from the current session
        sig_ts = sig.timestamp
        if hasattr(sig_ts, "astimezone"):
            sig_ts_et = sig_ts.astimezone(ET)
        else:
            sig_ts_et = sig_ts

        # Skip signals that pre-date today's session (multi-day bar history)
        if sig_ts_et.date() < now.date():
            continue

        # Dedup — also block if there is a pending-but-unfilled order
        if pm.has_position_for_symbol(symbol):
            logger.debug("Dedup: position already open for %s", symbol)
            continue
        if fill_tracker and fill_tracker.has_pending_for_symbol(symbol):
            logger.debug("Dedup: pending order already registered for %s", symbol)
            continue

        # Cooldown
        if pm.is_in_cooldown(now):
            logger.info("Cooldown active — skipping %s signal", symbol)
            if journal:
                await journal.record_rejection(
                    strategy_id=sig.strategy_id,
                    signal_direction=sig.direction.value,
                    underlying_symbol=symbol,
                    underlying_price=sig.price,
                    option_symbol=None,
                    rejection_reason="cooldown_after_loss",
                    entry_time=now,
                )
                await journal.commit()
            continue

        # Expirations
        try:
            expirations = await _retry(
                lambda: broker.get_available_expirations(symbol),
                label=f"expirations({symbol})",
            )
        except Exception:
            continue

        today = now.date()
        target_exp = None
        for dte in settings.options.preferred_dte:
            candidate = today + timedelta(days=dte)
            if candidate in expirations:
                target_exp = candidate
                break
        if target_exp is None and expirations:
            target_exp = min(expirations, key=lambda d: abs((d - today).days))
        if target_exp is None:
            continue

        # Option chain
        try:
            chain = await _retry(
                lambda: broker.get_option_chain(symbol, target_exp),
                label=f"chain({symbol})",
            )
        except Exception:
            continue

        # Stale quote check
        if _is_stale(chain.fetched_at, max_age_secs=90):
            logger.warning("Stale chain data for %s — skipping", symbol)
            continue

        # Liquidity filter
        contract = liq_filter.select_contract(chain, sig)
        if contract is None:
            if journal:
                await journal.record_rejection(
                    strategy_id=sig.strategy_id,
                    signal_direction=sig.direction.value,
                    underlying_symbol=symbol,
                    underlying_price=sig.price,
                    option_symbol=None,
                    rejection_reason="liquidity_filter_no_contract",
                    entry_time=now,
                )
                await journal.commit()
            continue

        # Risk check
        try:
            acct = await _retry(broker.get_account, label="get_account")
        except Exception:
            continue

        offset = Decimal(str(settings.options.limit_price_offset_pct))
        limit_price = (contract.ask * (1 + offset)).quantize(Decimal("0.01"))
        request = OrderRequest(
            symbol=symbol,
            option_symbol=contract.option_symbol,
            side=OrderSide.BUY_TO_OPEN,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=limit_price,
            strategy_id=sig.strategy_id,
            notes=sig.notes,
        )
        risk_result = risk.check_order(
            request=request,
            equity=acct.equity,
            contract=contract,
            now=now,
        )
        if not risk_result.passed:
            reason_str = "; ".join(risk_result.messages)
            logger.info("Risk rejected %s: %s", symbol, reason_str)
            if journal:
                await journal.record_rejection(
                    strategy_id=sig.strategy_id,
                    signal_direction=sig.direction.value,
                    underlying_symbol=symbol,
                    underlying_price=sig.price,
                    option_symbol=contract.option_symbol,
                    rejection_reason=reason_str,
                    entry_time=now,
                )
                await journal.commit()
            continue

        request.quantity = risk_result.approved_quantity

        # Place order
        if dry_run:
            logger.info(
                "DRY RUN: would place %s %s limit=%.2f",
                sig.direction.value, contract.option_symbol, float(limit_price),
            )
            placed += 1
            continue

        try:
            order = await _retry(
                lambda: broker.place_option_order(request),
                label="place_option_order",
            )
            risk.record_trade()
            logger.info(
                "Order placed: %s | %s | limit=%.2f | status=%s",
                order.order_id[:8], contract.option_symbol, float(limit_price), order.status.value,
            )

            if journal:
                journal_id = await journal.record_entry(
                    entry_time=now,
                    strategy_id=sig.strategy_id,
                    signal_direction=sig.direction.value,
                    underlying_symbol=symbol,
                    underlying_price=sig.price,
                    option_symbol=contract.option_symbol,
                    expiration=str(target_exp),
                    strike=float(contract.strike),
                    option_type=contract.option_type,
                    delta=contract.delta,
                    iv=contract.implied_volatility,
                    bid=float(contract.bid),
                    ask=float(contract.ask),
                    spread_pct=contract.spread_pct,
                    limit_price=float(limit_price),
                    quantity=request.quantity,
                    order_id=order.order_id,
                    notes=f"status={order.status.value}",
                )
                await journal.log_event(
                    event="order",
                    message=f"Placed {sig.direction.value} {contract.option_symbol} limit={float(limit_price):.2f}",
                    level="info",
                    symbol=symbol,
                    data={"order_id": order.order_id, "strategy": sig.strategy_id},
                )
                await journal.commit()

                if fill_tracker:
                    fill_tracker.register(
                        order_id=order.order_id,
                        journal_id=journal_id,
                        option_symbol=contract.option_symbol,
                        symbol=symbol,
                        strategy_id=sig.strategy_id,
                        direction=sig.direction.value,
                        quantity=request.quantity,
                        limit_price=float(limit_price),
                        placed_at=now,
                    )
                else:
                    # No fill tracker: open position immediately (legacy / dry-run)
                    pm.open(
                        option_symbol=contract.option_symbol,
                        symbol=symbol,
                        strategy_id=sig.strategy_id,
                        direction=sig.direction.value,
                        entry_time=now,
                        entry_price=float(contract.ask),
                        quantity=request.quantity,
                        journal_id=journal_id,
                    )
            placed += 1
        except Exception as exc:
            logger.error("Order placement failed for %s: %s", symbol, exc)

    return placed


# ── Main session loop ─────────────────────────────────────────────────────────

async def run_session(args: argparse.Namespace):
    global _shutdown_requested

    from app.api.models import AsyncSessionLocal, init_db
    from app.brokers import get_broker
    from app.config import get_settings
    from app.data import YFinanceDataSource
    from app.risk import RiskManager
    from app.strategies import IVCrushFilter, LiquidityFilter, OpeningRangeBreakoutStrategy, RSITrendStrategy, VWAPReclaimStrategy
    from app.trading import FillTracker, PositionManager, TradeJournal

    settings = get_settings()

    if settings.live_trading_enabled:
        logger.critical("LIVE_TRADING_ENABLED=true — session runner is for paper trading only. Aborting.")
        sys.exit(1)

    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    broker = get_broker(settings)
    data = YFinanceDataSource()
    risk = RiskManager(settings)
    pm = PositionManager(settings)
    fill_tracker = FillTracker(max_age_minutes=30)

    await init_db()
    db_session = AsyncSessionLocal()
    journal = TradeJournal(db_session, is_paper=True) if not args.dry_run else None

    iv_filter = IVCrushFilter({
        "earnings_blackout_days": settings.risk.earnings_blackout_days,
        "allow_earnings_trades": settings.risk.allow_earnings_trades,
    })
    liq_filter = LiquidityFilter({
        "min_open_interest": settings.risk.min_open_interest,
        "min_volume": settings.risk.min_volume,
        "max_spread_pct": settings.risk.max_spread_pct,
        "delta_target_min": settings.options.delta_target_min,
        "delta_target_max": settings.options.delta_target_max,
    })
    strategies = [
        OpeningRangeBreakoutStrategy(params={"range_minutes": 15, "min_range_pts": 0.5, "volume_confirmation": True}),
        VWAPReclaimStrategy(params={"proximity_pct": 0.002, "confirmation_bars": 2}),
        RSITrendStrategy(params={"rsi_period": 14, "rsi_oversold": 35, "trend_ema_period": 20}),
    ]

    # ── Session window ────────────────────────────────────────────────────────
    now = datetime.now(tz=ET)
    open_h, open_m = map(int, settings.market_open.split(":"))
    close_h, close_m = map(int, settings.market_close.split(":"))
    mkt_open  = now.replace(hour=open_h,  minute=open_m,  second=0, microsecond=0)
    mkt_close = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)

    eod_h, eod_m = map(int, settings.position.eod_exit_time.split(":"))
    eod_time = now.replace(hour=eod_h, minute=eod_m, second=0, microsecond=0)

    mode = "DRY RUN" if args.dry_run else "PAPER"
    logger.info(
        "Session runner starting | mode=%s | symbols=%s | poll=%ds",
        mode, args.symbols, args.poll,
    )
    print(f"\n{'═'*60}")
    print(f"  Session Runner — {now.strftime('%Y-%m-%d')}  [{mode}]")
    print(f"  Market: {mkt_open.strftime('%H:%M')} – {mkt_close.strftime('%H:%M')} ET")
    print(f"  EOD exit: {eod_time.strftime('%H:%M')} ET  |  Poll: every {args.poll}s")
    print(f"{'═'*60}\n")

    # Fetch account and start risk session
    try:
        acct = await _retry(broker.get_account, label="get_account")
        risk.start_session(acct.equity)
        logger.info("Account: equity=%.2f paper=%s", float(acct.equity), acct.is_paper)
    except Exception as exc:
        logger.error("Cannot fetch account at startup: %s — aborting", exc)
        await broker.close()
        await db_session.close()
        sys.exit(1)

    cycle = 0
    eod_liquidated = False
    session_placed = 0

    # ── Main polling loop ─────────────────────────────────────────────────────
    while not _shutdown_requested:
        now = datetime.now(tz=ET)
        cycle += 1

        # Kill switch
        if settings.is_kill_switch_active():
            logger.warning("Kill switch active — halting order placement")
            await asyncio.sleep(args.poll)
            continue

        # Before market open: wait
        if now < mkt_open:
            wait = (mkt_open - now).total_seconds()
            logger.info("Pre-market — waiting %.0fs until open", wait)
            await asyncio.sleep(min(wait, args.poll))
            continue

        # After market close: exit loop
        if now >= mkt_close:
            logger.info("Market closed — ending session")
            break

        logger.info("[cycle %d] %s ET | positions=%d | trades=%d",
                    cycle, now.strftime("%H:%M:%S"), len(pm.open_positions()), risk.trades_today)

        # Heartbeat log
        if journal:
            await journal.log_event(
                event="heartbeat",
                message=f"cycle={cycle} positions={len(pm.open_positions())} trades={risk.trades_today}",
                data={"cycle": cycle, "positions": len(pm.open_positions()), "pnl": float(risk.daily_pnl)},
            )
            await journal.commit()

        # EOD liquidation (before scanning for new entries)
        if now >= eod_time and not eod_liquidated:
            logger.warning("EOD time reached — cancelling pending orders and liquidating all positions")
            # Cancel all pending orders so they don't fill after we've liquidated
            if fill_tracker.count() > 0:
                await fill_tracker.poll(broker, pm, journal, now, risk)
            await eod_liquidate(broker, pm, journal, risk, now, args.dry_run)
            eod_liquidated = True
            # Don't scan for new entries after EOD
            await asyncio.sleep(args.poll)
            continue

        # Poll pending orders for fills / cancellations
        if fill_tracker.count() > 0:
            fills = await fill_tracker.poll(broker, pm, journal, now, risk)
            if fills:
                logger.info("FillTracker: %d fill(s) processed this cycle", fills)

        # Monitor + close existing positions
        closed = await monitor_positions(broker, pm, journal, risk, now, args.dry_run)
        if closed:
            logger.info("Closed %d position(s) this cycle", closed)

        # Only open new positions if not past EOD
        if now < eod_time:
            for symbol in args.symbols:
                placed = await scan_and_place(
                    symbol=symbol,
                    broker=broker,
                    data=data,
                    strategies=strategies,
                    iv_filter=iv_filter,
                    liq_filter=liq_filter,
                    risk=risk,
                    pm=pm,
                    journal=journal,
                    settings=settings,
                    now=now,
                    dry_run=args.dry_run,
                    fill_tracker=fill_tracker,
                )
                session_placed += placed

        if _shutdown_requested:
            break

        await asyncio.sleep(args.poll)

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    logger.info("Shutdown: liquidating %d remaining position(s)", len(pm.open_positions()))
    now = datetime.now(tz=ET)
    await eod_liquidate(broker, pm, journal, risk, now, args.dry_run)

    logger.info(
        "Session complete | cycles=%d | placed=%d | pnl=%.2f",
        cycle, session_placed, float(risk.daily_pnl),
    )
    print(f"\n  Session complete — {cycle} cycles | {session_placed} orders | P&L ${float(risk.daily_pnl):.2f}\n")

    await broker.close()
    await db_session.close()


# ── Entry point ───────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Continuous intraday paper-trading session runner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--dry-run", action="store_true", help="No orders placed")
    p.add_argument("--symbol", "--symbols", nargs="+", dest="symbols", default=["SPY"], metavar="SYM")
    p.add_argument("--poll", type=int, default=300, metavar="SECONDS",
                   help="Polling interval in seconds (default: 300 = 5 min)")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    # Set up rotating JSON-capable logs before anything else
    from app.utils.logging_setup import configure_logging
    configure_logging(level=args.log_level)

    asyncio.run(run_session(args))
