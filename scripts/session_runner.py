"""
Daily Session Runner — continuous intraday paper-trading loop.

Runs from market open until market close, polling every POLL_INTERVAL seconds.
Each poll cycle:
  1. Fill tracker poll (update pending order statuses from broker)
  2. Position monitor (fetch quotes, evaluate exit conditions)
  3. Signal scan + order placement (if before EOD)
  4. Broker reconciliation (every --reconcile-interval minutes)
  5. Heartbeat log

Safety guarantees:
  • LIVE_TRADING_ENABLED=true aborts immediately.
  • Kill switch file respected on every cycle.
  • All positions force-closed at EOD_EXIT_TIME.
  • SIGTERM / SIGINT trigger graceful shutdown:
      – one final fill poll
      – optional pending-order cancellation (--cancel-pending)
      – position liquidation
      – session health report written to logs/
  • API failures are retried with exponential backoff; after MAX_RETRIES the
    cycle is skipped — the runner never crashes.
  • On restart, pending orders are reloaded from DB and broker positions
    are reconciled so no duplicate orders are placed.

Usage:
  python scripts/session_runner.py                # runs today's session
  python scripts/session_runner.py --dry-run      # no orders placed
  python scripts/session_runner.py --symbol QQQ SPY
  python scripts/session_runner.py --poll 60      # poll every 60 s
  python scripts/session_runner.py --cancel-pending   # cancel open orders on shutdown
  python scripts/session_runner.py --reconcile-interval 15  # recon every 15 min
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
    alert_service=None,
    settings=None,
) -> int:
    """
    For every open position, fetch a live quote, check exit conditions,
    and close if triggered.  Returns number of positions closed.
    """
    closed = 0
    for pos in list(pm.open_positions()):
        # Fetch live quote with retry
        _exit_bid: float | None = None
        _exit_ask: float | None = None
        try:
            quote = await _retry(
                lambda p=pos: broker.get_option_quote(p.option_symbol),
                label=f"get_option_quote({pos.option_symbol})",
            )
            current_price = float(quote.mid) if float(quote.mid) > 0 else pos.entry_price
            _exit_bid = float(quote.bid)
            _exit_ask = float(quote.ask)
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

            # ── Exit spread awareness ──────────────────────────────────────
            # Warn when spread is wide, but never block emergency exits.
            _emergency_reasons = ("stop_loss", "eod_exit")
            if (
                settings is not None
                and _exit_bid is not None
                and _exit_ask is not None
                and _exit_ask > 0
            ):
                _mid = (_exit_bid + _exit_ask) / 2
                if _mid > 0:
                    _spread_pct = (_exit_ask - _exit_bid) / _mid
                    _max_spread = getattr(settings.risk, "max_spread_pct", 0.10)
                    if _spread_pct > _max_spread:
                        logger.warning(
                            "Exit spread warning | %s | spread_pct=%.3f > max=%.3f | "
                            "bid=%.4f ask=%.4f | reason=%s",
                            pos.option_symbol, _spread_pct, _max_spread,
                            _exit_bid, _exit_ask, reason,
                        )
                        if journal:
                            await journal.log_event(
                                event="exit_spread_warning",
                                message=(
                                    f"{pos.option_symbol} wide spread on exit: "
                                    f"spread_pct={_spread_pct:.3f} > max={_max_spread:.3f}"
                                ),
                                level="warning",
                                symbol=pos.symbol,
                                data={
                                    "reason": reason,
                                    "spread_pct": round(_spread_pct, 4),
                                    "max_spread_pct": _max_spread,
                                    "bid": _exit_bid,
                                    "ask": _exit_ask,
                                    "is_emergency": reason in _emergency_reasons,
                                },
                            )
                            await journal.commit()

            if not dry_run:
                # Place exit order with broker before removing from local state
                from app.brokers.broker_interface import (
                    OrderRequest as _OReq,
                    OrderSide as _OSide,
                    OrderType as _OType,
                )
                _exit_lp = Decimal(str(round(
                    _exit_bid if (_exit_bid or 0) > 0 else current_price * 0.98, 2
                )))
                _exit_req = _OReq(
                    symbol=pos.symbol,
                    option_symbol=pos.option_symbol,
                    side=_OSide.SELL_TO_CLOSE,
                    quantity=pos.quantity,
                    order_type=_OType.LIMIT,
                    limit_price=_exit_lp,
                    strategy_id=pos.strategy_id,
                    notes=f"exit:{reason}",
                )
                try:
                    _exit_order = await _retry(
                        lambda: broker.place_option_order(_exit_req),
                        label=f"exit_order({pos.option_symbol})",
                    )
                    logger.info(
                        "Exit order placed | %s | limit=%.4f | order_id=%s",
                        pos.option_symbol, float(_exit_lp), _exit_order.order_id,
                    )
                except Exception as _exit_exc:
                    logger.error(
                        "Exit order placement failed for %s: %s — position closed locally only",
                        pos.option_symbol, _exit_exc,
                    )
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
                        exit_bid=_exit_bid,
                        exit_ask=_exit_ask,
                    )
                    await journal.log_event(
                        event="exit",
                        message=f"{pos.option_symbol} closed: {reason} pnl={pnl:.2f}",
                        level="info",
                        symbol=pos.symbol,
                        data={"reason": reason, "pnl": round(pnl, 2), "hold_secs": round(hold_secs)},
                    )
                    await journal.commit()
                if alert_service:
                    from app.utils.alerting import AlertEvent
                    if reason == "stop_loss":
                        alert_event = AlertEvent.STOP_LOSS
                    elif reason in ("take_profit", "trailing_stop"):
                        alert_event = AlertEvent.TAKE_PROFIT
                    else:
                        alert_event = None
                    if alert_event:
                        await alert_service.send(
                            alert_event,
                            f"{pos.option_symbol} {reason} pnl={pnl:.2f}",
                            data={"symbol": pos.symbol, "reason": reason, "pnl": round(pnl, 2)},
                        )
            else:
                logger.info("DRY RUN: would close %s (%s)", pos.option_symbol, reason)
                pm.close(pos.option_symbol, current_price, pnl)
            closed += 1
    return closed


# ── EOD force-close all positions ────────────────────────────────────────────

async def eod_liquidate(broker, pm, journal, risk, now: datetime, dry_run: bool, settings=None):
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
            _eod_bid_pre = float(quote.bid) if float(quote.bid) > 0 else None
            _eod_ask_pre = float(quote.ask) if float(quote.ask) > 0 else None
        except Exception:
            exit_price = pos.entry_price
            _eod_bid_pre = None
            _eod_ask_pre = None

        pnl = (exit_price - pos.entry_price) * 100 * pos.quantity
        hold_secs = (now - pos.entry_time).total_seconds()

        # ── Exit spread awareness (EOD is always emergency — log only) ────
        if (
            settings is not None
            and _eod_bid_pre is not None
            and _eod_ask_pre is not None
            and _eod_ask_pre > 0
        ):
            _eod_mid = (_eod_bid_pre + _eod_ask_pre) / 2
            if _eod_mid > 0:
                _eod_spread_pct = (_eod_ask_pre - _eod_bid_pre) / _eod_mid
                _eod_max_spread = getattr(settings.risk, "max_spread_pct", 0.10)
                if _eod_spread_pct > _eod_max_spread:
                    logger.warning(
                        "Exit spread warning | %s | spread_pct=%.3f > max=%.3f | "
                        "bid=%.4f ask=%.4f | reason=eod_exit (proceeding)",
                        pos.option_symbol, _eod_spread_pct, _eod_max_spread,
                        _eod_bid_pre, _eod_ask_pre,
                    )
                    if journal:
                        await journal.log_event(
                            event="exit_spread_warning",
                            message=(
                                f"{pos.option_symbol} wide spread on EOD exit: "
                                f"spread_pct={_eod_spread_pct:.3f} > max={_eod_max_spread:.3f}"
                            ),
                            level="warning",
                            symbol=pos.symbol,
                            data={
                                "reason": "eod_exit",
                                "spread_pct": round(_eod_spread_pct, 4),
                                "max_spread_pct": _eod_max_spread,
                                "bid": _eod_bid_pre,
                                "ask": _eod_ask_pre,
                                "is_emergency": True,
                            },
                        )
                        await journal.commit()

        if not dry_run:
            # Place exit order with broker before removing from local state
            from app.brokers.broker_interface import (
                OrderRequest as _OReq,
                OrderSide as _OSide,
                OrderType as _OType,
            )
            try:
                _eod_quote = await _retry(
                    lambda p=pos: broker.get_option_quote(p.option_symbol),
                    label=f"eod_exit_quote({pos.option_symbol})",
                )
                _eod_bid = float(_eod_quote.bid) if float(_eod_quote.bid) > 0 else exit_price * 0.98
            except Exception:
                _eod_bid = exit_price * 0.98
            _eod_lp = Decimal(str(round(_eod_bid, 2)))
            _eod_req = _OReq(
                symbol=pos.symbol,
                option_symbol=pos.option_symbol,
                side=_OSide.SELL_TO_CLOSE,
                quantity=pos.quantity,
                order_type=_OType.LIMIT,
                limit_price=_eod_lp,
                strategy_id=pos.strategy_id,
                notes="exit:eod_exit",
            )
            try:
                _eod_order = await _retry(
                    lambda: broker.place_option_order(_eod_req),
                    label=f"eod_exit_order({pos.option_symbol})",
                )
                logger.info(
                    "EOD exit order placed | %s | limit=%.4f | order_id=%s",
                    pos.option_symbol, float(_eod_lp), _eod_order.order_id,
                )
            except Exception as _eod_exc:
                logger.error(
                    "EOD exit order failed for %s: %s — closed locally only",
                    pos.option_symbol, _eod_exc,
                )
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


# ── No-signal diagnostics ────────────────────────────────────────────────────

def _diagnose_no_signals(bars, symbol: str, strat_counts: dict, log) -> None:
    """Log VWAP and RSI state when no actionable signals are produced."""
    import numpy as np

    close = bars["close"]
    volume = bars.get("volume", None)

    # VWAP
    try:
        if volume is not None and not volume.empty:
            typical = (bars["high"] + bars["low"] + close) / 3
            vwap = (typical * volume).cumsum() / volume.cumsum()
            price_now = float(close.iloc[-1])
            vwap_now = float(vwap.iloc[-1])
            proximity_pct = abs(price_now - vwap_now) / max(vwap_now, 0.01) * 100
            side = "above" if price_now > vwap_now else "below"
            crosses = int(((close > vwap) != (close > vwap).shift(1)).sum())
            log.info(
                "[diag:%s] VWAP: price=%.2f vwap=%.2f (%.2f%% %s) crosses_today=%d raw_vwap_signals=%d",
                symbol, price_now, vwap_now, proximity_pct, side, crosses,
                strat_counts.get("VWAPReclaimStrategy", 0),
            )
    except Exception as exc:
        log.debug("[diag:%s] VWAP diagnostic error: %s", symbol, exc)

    # RSI + EMA
    try:
        if len(close) >= 14:
            delta = close.diff()
            gain = delta.clip(lower=0)
            loss = (-delta).clip(lower=0)
            avg_gain = gain.ewm(span=14, adjust=False).mean()
            avg_loss = loss.ewm(span=14, adjust=False).mean()
            rs = avg_gain / avg_loss.replace(0, float("nan"))
            rsi = (100 - 100 / (1 + rs)).iloc[-1]
            ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
            price_now = float(close.iloc[-1])
            log.info(
                "[diag:%s] RSI(14)=%.1f (oversold<35, overbought>70) EMA(20)=%.2f price=%.2f %s raw_rsi_signals=%d",
                symbol, rsi, ema20, price_now,
                "ABOVE_EMA" if price_now > ema20 else "BELOW_EMA",
                strat_counts.get("RSITrendStrategy", 0),
            )
    except Exception as exc:
        log.debug("[diag:%s] RSI diagnostic error: %s", symbol, exc)


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
    store=None,
    session_date: str = "",
    alert_service=None,
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
    strat_counts: dict = {}
    for strat in strategies:
        sigs = strat.generate_signals(bars, symbol)
        strat_counts[type(strat).__name__] = len(sigs)
        all_signals.extend(sigs)

    # IV filter
    filtered = iv_filter.apply(all_signals)
    actionable = [s for s in filtered if s.is_actionable()]

    if not actionable:
        _diagnose_no_signals(bars, symbol, strat_counts, logger)

    # Realistic fill test mode: SPY only
    if getattr(settings, "realistic_fill_test_mode", False) and symbol != "SPY":
        logger.info("REALISTIC_FILL_TEST_MODE: skipping non-SPY symbol %s", symbol)
        return 0

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

        # ── Determine limit price based on configured pricing mode ────────────
        from app.trading.pricing import compute_limit_price as _clp

        _fill_test = getattr(settings, "realistic_fill_test_mode", False)

        # Safety: realistic_fill_test_mode requires a paper account
        if _fill_test and not acct.is_paper:
            logger.critical("REALISTIC_FILL_TEST_MODE: non-paper account detected — aborting session")
            import sys as _sys
            _sys.exit(1)

        # Determine effective mode (fill test overrides to marketable_limit)
        _entry_mode = getattr(settings.options, "entry_limit_price_mode", "mid")
        if _fill_test:
            _entry_mode = "marketable_limit"

        # Hard-stop if spread is too wide in fill test mode
        if _fill_test:
            _spread = float(contract.ask) - float(contract.bid)
            _mid = float(contract.mid)
            _max_spread = getattr(settings, "fill_test_max_spread_pct", 0.20)
            if _mid > 0 and _spread / _mid > _max_spread:
                logger.warning(
                    "REALISTIC_FILL_TEST_MODE: spread %.1f%% > threshold %.1f%% for %s — skipping",
                    _spread / _mid * 100, _max_spread * 100, contract.option_symbol,
                )
                continue

        _raw_lp = _clp(
            mode=_entry_mode,
            bid=float(contract.bid),
            ask=float(contract.ask),
            offset_pct=getattr(settings.options, "entry_marketable_offset_pct", 0.01),
        )
        limit_price = Decimal(str(_raw_lp))

        # Guard: duplicate option contract (underlying dedup happens upstream;
        # this catches the exact same option_symbol being re-entered).
        if pm.has_position(contract.option_symbol):
            logger.info(
                "Dedup: already hold %s — skipping duplicate entry",
                contract.option_symbol,
            )
            continue

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
        # Fill test enforces qty=1 regardless of risk manager approval
        if _fill_test:
            request.quantity = 1
        # Hard cap at max_contracts_per_position (default 1)
        _max_cpp = getattr(settings.universe, "max_contracts_per_position", 1)
        if request.quantity > _max_cpp:
            logger.info(
                "Capping quantity %d→%d (max_contracts_per_position=%d)",
                request.quantity, _max_cpp, _max_cpp,
            )
            request.quantity = _max_cpp

        # Place order
        if dry_run:
            logger.info(
                "DRY RUN: would place %s %s limit=%.2f",
                sig.direction.value, contract.option_symbol, float(limit_price),
            )
            placed += 1
            continue

        order = None
        used_contract = contract
        used_exp = target_exp
        try:
            order = await _retry(
                lambda: broker.place_option_order(request),
                label="place_option_order",
            )
        except Exception as exc:
            err_str = str(exc)
            # 422 = Alpaca rejected expired/non-tradeable contract; try next future expiry
            if "422" in err_str and target_exp <= now.date():
                logger.warning(
                    "422 on today-expiry %s for %s — trying next future expiry",
                    target_exp, contract.option_symbol,
                )
                try:
                    future_exps = [e for e in expirations if e > now.date()]
                    if not future_exps:
                        logger.error("No future expirations available for %s", symbol)
                    else:
                        alt_exp = min(future_exps)
                        alt_chain = await _retry(
                            lambda: broker.get_option_chain(symbol, alt_exp),
                            label=f"alt_chain({symbol})",
                        )
                        alt_contract = liq_filter.select_contract(alt_chain, sig)
                        if alt_contract is None:
                            logger.warning("Liquidity filter found no contract on alt expiry %s", alt_exp)
                        else:
                            alt_lp = Decimal(str(_clp(
                                mode=_entry_mode,
                                bid=float(alt_contract.bid),
                                ask=float(alt_contract.ask),
                                offset_pct=getattr(settings.options, "entry_marketable_offset_pct", 0.01),
                            )))
                            alt_request = OrderRequest(
                                symbol=symbol,
                                option_symbol=alt_contract.option_symbol,
                                side=request.side,
                                quantity=request.quantity,
                                order_type=request.order_type,
                                limit_price=alt_lp,
                                strategy_id=sig.strategy_id,
                                notes=f"{sig.notes} [next-expiry-fallback]",
                            )
                            order = await _retry(
                                lambda: broker.place_option_order(alt_request),
                                label="place_option_order_alt",
                            )
                            used_contract = alt_contract
                            used_exp = alt_exp
                            limit_price = alt_lp
                            request = alt_request
                            logger.info(
                                "422 fallback order placed: %s | exp=%s | limit=%.2f",
                                alt_contract.option_symbol, alt_exp, float(alt_lp),
                            )
                except Exception as fallback_exc:
                    logger.error("422 fallback also failed for %s: %s", symbol, fallback_exc)
            else:
                logger.error("Order placement failed for %s: %s", symbol, exc)

        if order is None:
            continue

        risk.record_trade()
        logger.info(
            "Order placed: %s | %s | limit=%.2f | status=%s",
            order.order_id[:8], used_contract.option_symbol, float(limit_price), order.status.value,
        )

        if journal:
            journal_id = await journal.record_entry(
                entry_time=now,
                strategy_id=sig.strategy_id,
                signal_direction=sig.direction.value,
                underlying_symbol=symbol,
                underlying_price=sig.price,
                option_symbol=used_contract.option_symbol,
                expiration=str(used_exp),
                strike=float(used_contract.strike),
                option_type=used_contract.option_type,
                delta=used_contract.delta,
                iv=used_contract.implied_volatility,
                bid=float(used_contract.bid),
                ask=float(used_contract.ask),
                spread_pct=used_contract.spread_pct,
                limit_price=float(limit_price),
                limit_price_mode=_entry_mode,
                quantity=request.quantity,
                order_id=order.order_id,
                notes=f"status={order.status.value} mode={_entry_mode}",
            )
            await journal.log_event(
                event="order",
                message=f"Placed {sig.direction.value} {used_contract.option_symbol} limit={float(limit_price):.2f}",
                level="info",
                symbol=symbol,
                data={"order_id": order.order_id, "strategy": sig.strategy_id},
            )
            await journal.commit()

            if alert_service:
                from app.utils.alerting import AlertEvent
                await alert_service.send(
                    AlertEvent.ORDER_SUBMITTED,
                    f"{sig.direction.value} {used_contract.option_symbol} limit={float(limit_price):.2f}",
                    data={
                        "order_id": order.order_id,
                        "symbol": symbol,
                        "strategy": sig.strategy_id,
                        "limit_price": float(limit_price),
                        "quantity": request.quantity,
                    },
                )

            if fill_tracker:
                po = fill_tracker.register(
                    order_id=order.order_id,
                    journal_id=journal_id,
                    option_symbol=used_contract.option_symbol,
                    symbol=symbol,
                    strategy_id=sig.strategy_id,
                    direction=sig.direction.value,
                    quantity=request.quantity,
                    limit_price=float(limit_price),
                    placed_at=now,
                )
                # Persist so the order survives a crash/restart
                if store and session_date:
                    await store.save(po, session_date)
            else:
                # No fill tracker: open position immediately (legacy / dry-run)
                pm.open(
                    option_symbol=used_contract.option_symbol,
                    symbol=symbol,
                    strategy_id=sig.strategy_id,
                    direction=sig.direction.value,
                    entry_time=now,
                    entry_price=float(used_contract.ask),
                    quantity=request.quantity,
                    journal_id=journal_id,
                )
        placed += 1

    return placed


# ── Universe scan helper ──────────────────────────────────────────────────────

async def _run_universe_scan(
    settings,
    broker,
    journal,
    session_date: str,
    scan_store: Optional[dict] = None,
) -> Optional[List[str]]:
    """
    Run the scanning pipeline and return a ranked list of confirmed symbols.

    Steps:
      1. Load universe YAML → get candidate symbol list
      2. YFinanceScanner → compute intraday metrics (research only)
      3. CandidateScorer → rank and filter symbols
      4. AlpacaConfirmer → verify option chain liquidity
      5. Persist results to DBScanResult (if journal available)

    Returns:
      None          — STANDBY: all candidates rejected, CLI fallback blocked.
      []            — fallback allowed (allow_cli_fallback_when_scanner_rejects=True
                      and rvol gate passed) but no confirmed symbols.
      [sym, ...]    — confirmed symbol names, best first.
    """
    import json as _json
    from app.scanning import AlpacaConfirmer, CandidateScorer, UniverseLoader, YFinanceScanner
    from app.api.models import DBScanResult

    uni = settings.universe
    max_scan  = uni.max_symbols_per_scan
    max_sym   = uni.max_active_symbols
    min_score = uni.min_scan_score

    # Load universe
    loader = UniverseLoader()
    loader.load()
    if loader.mode == "off":
        logger.info("Universe mode=off — skipping scan, using arg symbols")
        return []

    all_syms = loader.get_symbols(max_symbols=max_scan)
    if not all_syms:
        logger.warning("Universe empty — no symbols to scan")
        return []

    logger.info("Universe scan: %d symbols | max_active=%d", len(all_syms), max_sym)

    # YFinance scan
    scanner = YFinanceScanner()
    metrics_list = await scanner.scan(all_syms)

    # Score
    scorer = CandidateScorer(min_scan_score=min_score)
    candidates = scorer.score_all(metrics_list)

    passed    = [c for c in candidates if not c.is_rejected]
    rejected  = [c for c in candidates if c.is_rejected]
    logger.info(
        "Scan: %d passed, %d rejected",
        len(passed), len(rejected),
    )

    for c in candidates[:5]:
        logger.info(
            "  %-6s  score=%.1f  signal=%-7s  %s",
            c.symbol, c.score, c.signal_type,
            ("REJECTED: " + ", ".join(c.rejected_reasons)) if c.is_rejected else ("reasons: " + ", ".join(c.reason_codes[:3])),
        )

    # ── STANDBY guard ─────────────────────────────────────────────────────────
    # When every candidate is rejected, block CLI fallback unless explicitly
    # allowed AND the rvol gate passes.
    if len(passed) == 0 and len(candidates) > 0:
        uni = settings.universe
        allow_fallback = getattr(uni, "allow_cli_fallback_when_scanner_rejects", False)
        fallback_min_rvol = getattr(uni, "fallback_min_rvol", 0.20)
        max_rvol = max((c.metrics.rvol or 0.0) for c in candidates)

        if not allow_fallback:
            standby_reason = (
                f"all_{len(candidates)}_candidates_rejected_fallback_disabled"
            )
        elif max_rvol < fallback_min_rvol:
            standby_reason = (
                f"all_candidates_rejected_max_rvol={max_rvol:.3f}_below_{fallback_min_rvol}"
            )
        else:
            standby_reason = None  # fallback is permitted

        _cand_payload = [
            {
                "symbol": c.symbol,
                "score": c.score,
                "signal_type": c.signal_type,
                "is_rejected": c.is_rejected,
                "reason_codes": c.reason_codes,
                "rejected_reasons": c.rejected_reasons,
            }
            for c in candidates
        ]

        if standby_reason is not None:
            logger.warning("STANDBY: %s", standby_reason)
            if journal:
                await journal.log_event(
                    event="standby",
                    message=f"Scanner STANDBY: {standby_reason}",
                    level="warning",
                    data={
                        "reason": standby_reason,
                        "candidates_rejected": len(candidates),
                        "max_rvol": round(max_rvol, 4),
                    },
                )
                await journal.commit()
            if scan_store is not None:
                scan_store.clear()
                scan_store.update({
                    "session_date": session_date,
                    "scanned_at": datetime.now(tz=ET).isoformat(),
                    "standby": True,
                    "standby_reason": standby_reason,
                    "confirmed": [],
                    "candidates": _cand_payload,
                })
            return None
        else:
            logger.info(
                "STANDBY-FALLBACK: all candidates rejected but CLI fallback allowed "
                "(max_rvol=%.3f >= %.3f)",
                max_rvol, fallback_min_rvol,
            )
            if scan_store is not None:
                scan_store.clear()
                scan_store.update({
                    "session_date": session_date,
                    "scanned_at": datetime.now(tz=ET).isoformat(),
                    "standby": False,
                    "standby_reason": None,
                    "confirmed": [],
                    "candidates": _cand_payload,
                })
            return []

    # Alpaca confirmation
    confirmer = AlpacaConfirmer(broker, settings)
    confirmed = await confirmer.confirm_all(passed[:max_sym * 2])  # over-sample, then cap

    confirmed_syms = [cc.symbol for cc in confirmed[:max_sym]]
    logger.info("AlpacaConfirmer: %d confirmed symbols: %s", len(confirmed_syms), confirmed_syms)

    # Persist scan results to DB
    if journal:
        try:
            for c in candidates:
                row = DBScanResult(
                    session_date=session_date,
                    symbol=c.symbol,
                    score=c.score,
                    signal_type=c.signal_type,
                    reason_codes=_json.dumps(c.reason_codes),
                    rejected_reasons=_json.dumps(c.rejected_reasons),
                    is_rejected=c.is_rejected,
                    selected=c.symbol in confirmed_syms,
                    atr_pct=c.metrics.atr_pct,
                    rvol=c.metrics.rvol,
                    rsi=c.metrics.rsi,
                    vwap=c.metrics.vwap,
                    price=c.metrics.price,
                    price_vs_vwap=c.metrics.price_vs_vwap,
                    gap_pct=c.metrics.gap_pct,
                    trend=c.metrics.trend,
                    ma_compression=c.metrics.ma_compression,
                    has_earnings=c.metrics.has_earnings_today,
                )
                journal._db.add(row)
            await journal.commit()
        except Exception as exc:
            logger.warning("Failed to persist scan results: %s", exc)

    # Update in-memory scan store (for dashboard)
    if scan_store is not None:
        scan_store.clear()
        scan_store["session_date"] = session_date
        scan_store["scanned_at"] = datetime.now(tz=ET).isoformat()
        scan_store["standby"] = False
        scan_store["standby_reason"] = None
        scan_store["confirmed"] = confirmed_syms
        scan_store["candidates"] = [
            {
                "symbol": c.symbol,
                "score": c.score,
                "signal_type": c.signal_type,
                "is_rejected": c.is_rejected,
                "reason_codes": c.reason_codes,
                "rejected_reasons": c.rejected_reasons,
            }
            for c in candidates
        ]

    return confirmed_syms


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
    from app.trading.health_report import HealthReporter
    from app.trading.pending_order_store import PendingOrderStore
    from app.trading.reconciler import Reconciler
    from app.trading.session_recovery import SessionRecovery
    from app.utils.alerting import AlertConfig, AlertEvent, AlertService

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

    alert_service = AlertService(AlertConfig.from_env())

    await init_db()
    db_session = AsyncSessionLocal()
    journal = TradeJournal(db_session, is_paper=True) if not args.dry_run else None
    store = PendingOrderStore(db_session) if not args.dry_run else None
    _timeout_min = getattr(settings, "entry_order_timeout_secs", 120) / 60
    fill_tracker = FillTracker(max_age_minutes=_timeout_min, store=store, alert_service=alert_service)

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

    # ── Runtime stats (for health report) ────────────────────────────────────
    api_errors: int = 0
    recon_warnings: List[str] = []
    today_str = datetime.now(tz=ET).strftime("%Y-%m-%d")

    # ── Session window ────────────────────────────────────────────────────────
    now = datetime.now(tz=ET)
    open_h, open_m = map(int, settings.market_open.split(":"))
    close_h, close_m = map(int, settings.market_close.split(":"))
    mkt_open  = now.replace(hour=open_h,  minute=open_m,  second=0, microsecond=0)
    mkt_close = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)

    eod_h, eod_m = map(int, settings.position.eod_exit_time.split(":"))
    eod_time = now.replace(hour=eod_h, minute=eod_m, second=0, microsecond=0)

    _fill_test_mode = getattr(settings, "realistic_fill_test_mode", False)
    _uni_mode_pre = getattr(settings.universe, "mode", "off")
    mode = "DRY RUN" if args.dry_run else ("FILL-TEST" if _fill_test_mode else "PAPER")
    logger.info(
        "Session runner starting | mode=%s | symbols=%s | universe=%s | poll=%ds",
        mode, args.symbols, _uni_mode_pre, args.poll,
    )
    if _fill_test_mode:
        logger.info(
            "REALISTIC_FILL_TEST_MODE: marketable_limit pricing | SPY only | qty=1 | "
            "entry_timeout=%ds | max_spread=%.0f%%",
            getattr(settings, "entry_order_timeout_secs", 120),
            getattr(settings, "fill_test_max_spread_pct", 0.20) * 100,
        )
    print(f"\n{'═'*60}")
    print(f"  Session Runner — {now.strftime('%Y-%m-%d')}  [{mode}]")
    print(f"  Market: {mkt_open.strftime('%H:%M')} – {mkt_close.strftime('%H:%M')} ET")
    print(f"  EOD exit: {eod_time.strftime('%H:%M')} ET  |  Poll: every {args.poll}s")
    print(f"{'═'*60}\n")

    # ── Pre-session checklist (evaluation mode) ───────────────────────────────
    if settings.paper_evaluation_mode:
        from app.evaluation.pre_session import (
            all_required_pass,
            format_check_table,
            run_pre_session_checks,
        )
        checks = await run_pre_session_checks(
            settings=settings,
            broker=broker,
            db_session=db_session,
            risk_manager=risk,
        )
        table = format_check_table(checks)
        print(table)
        logger.info("Pre-session checklist results:\n%s", table)
        if not all_required_pass(checks):
            logger.critical("Pre-session checks FAILED — refusing to start session")
            print("\n  Pre-session checks FAILED — session aborted.\n")
            await broker.close()
            await db_session.close()
            sys.exit(2)
        logger.info("Pre-session checks PASSED — proceeding")

    # Fetch account and start risk session
    try:
        acct = await _retry(broker.get_account, label="get_account")
        risk.start_session(acct.equity)
        logger.info("Account: equity=%.2f paper=%s", float(acct.equity), acct.is_paper)
        await alert_service.send(
            AlertEvent.SESSION_STARTED,
            f"Paper session started | equity={float(acct.equity):.2f} | symbols={args.symbols}",
            data={"mode": mode, "symbols": args.symbols, "equity": float(acct.equity)},
        )
    except Exception as exc:
        logger.error("Cannot fetch account at startup: %s — aborting", exc)
        await broker.close()
        await db_session.close()
        sys.exit(1)

    # ── Startup recovery ──────────────────────────────────────────────────────
    if store:
        try:
            recovery_result = await SessionRecovery().recover(
                broker, pm, fill_tracker, store, today_str
            )
            for w in recovery_result.warnings:
                logger.warning("Recovery: %s", w)
                recon_warnings.append(w)
            for e in recovery_result.errors:
                logger.error("Recovery: %s", e)
                api_errors += 1
            if recovery_result.pending_orders_loaded or recovery_result.broker_positions_loaded:
                logger.info(
                    "Recovery loaded %d pending order(s), %d broker position(s)",
                    recovery_result.pending_orders_loaded,
                    recovery_result.broker_positions_loaded,
                )
        except Exception as exc:
            logger.error("Startup recovery failed: %s", exc)
            api_errors += 1

    reconciler = Reconciler()
    last_reconciled_at: Optional[datetime] = None

    # ── Universe scan (pre-session) ───────────────────────────────────────────
    _scan_store: dict = {}
    active_symbols: List[str] = []
    _uni_mode = getattr(settings.universe, "mode", "off")

    if _fill_test_mode:
        logger.info("REALISTIC_FILL_TEST_MODE: universe scan disabled — using SPY only")
        active_symbols = ["SPY"]
    elif _uni_mode == "off":
        active_symbols = list(args.symbols)
    else:
        logger.info("Running pre-session universe scan (mode=%s) …", _uni_mode)
        try:
            scanned = await _run_universe_scan(
                settings=settings,
                broker=broker,
                journal=journal,
                session_date=today_str,
                scan_store=_scan_store,
            )
            if scanned is None:
                logger.warning(
                    "STANDBY: scanner rejected all candidates, fallback blocked — "
                    "no new entries this session"
                )
            elif scanned:
                active_symbols = scanned
                logger.info("Active symbols from scan: %s", active_symbols)
            else:
                logger.warning(
                    "Scan returned no confirmed symbols — falling back to %s", args.symbols
                )
                active_symbols = list(args.symbols)
        except Exception as exc:
            logger.error("Pre-session scan failed: %s — using arg symbols", exc)
            active_symbols = list(args.symbols)

    _max_active_pos = getattr(settings.universe, "max_active_positions", 1)
    _max_sym_per_day = getattr(settings.universe, "max_symbols_traded_per_day", 1)
    _last_scan_at: Optional[datetime] = datetime.now(tz=ET) if _uni_mode != "off" else None
    _scan_interval_min = getattr(settings.universe, "scan_interval_minutes", 30)
    symbols_traded_today: set = set()
    _recon_has_mismatch: bool = False  # set True while reconciliation flags mismatches

    # Publish initial active_symbols list to shared scan_store for dashboard
    _scan_store["active_symbols"] = list(active_symbols)

    cycle = 0
    eod_liquidated = False
    session_placed = 0
    kill_switch_alerted = False

    # ── Main polling loop ─────────────────────────────────────────────────────
    while not _shutdown_requested:
        now = datetime.now(tz=ET)
        cycle += 1

        # Kill switch
        if settings.is_kill_switch_active():
            logger.warning("Kill switch active — halting order placement")
            if not kill_switch_alerted:
                await alert_service.send(
                    AlertEvent.KILL_SWITCH,
                    "Kill switch activated — order placement halted",
                    data={"cycle": cycle},
                )
                kill_switch_alerted = True
            await asyncio.sleep(args.poll)
            continue
        else:
            kill_switch_alerted = False

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
            n_pos = len(pm.open_positions())
            await eod_liquidate(broker, pm, journal, risk, now, args.dry_run, settings=settings)
            eod_liquidated = True
            await alert_service.send(
                AlertEvent.EOD_LIQUIDATION,
                f"EOD liquidation: closed {n_pos} position(s)",
                data={"positions_closed": n_pos, "daily_pnl": float(risk.daily_pnl)},
            )
            # Don't scan for new entries after EOD
            await asyncio.sleep(args.poll)
            continue

        # Poll pending orders for fills / cancellations
        if fill_tracker.count() > 0:
            try:
                fills = await fill_tracker.poll(broker, pm, journal, now, risk)
                if fills:
                    logger.info("FillTracker: %d fill(s) processed this cycle", fills)
            except Exception as exc:
                logger.error("FillTracker poll error: %s", exc)
                api_errors += 1
                await alert_service.send(
                    AlertEvent.API_ERROR,
                    f"FillTracker poll error: {exc}",
                    data={"cycle": cycle, "total_errors": api_errors},
                )

        # Periodic broker reconciliation
        recon_due = (
            last_reconciled_at is None
            or (now - last_reconciled_at).total_seconds() / 60 >= args.reconcile_interval
        )
        if recon_due:
            try:
                recon = await reconciler.reconcile(broker, pm, fill_tracker, now)
                recon_warnings.extend(recon.flagged)
                last_reconciled_at = now
                if recon.flagged:
                    _recon_has_mismatch = True
                    logger.warning(
                        "Reconciliation flagged %d mismatch(es) — new entries blocked "
                        "until next clean reconcile: %s",
                        len(recon.flagged), recon.flagged,
                    )
                else:
                    _recon_has_mismatch = False
            except Exception as exc:
                logger.warning("Reconciliation error: %s", exc)
                api_errors += 1

        # Monitor + close existing positions
        closed = await monitor_positions(broker, pm, journal, risk, now, args.dry_run, alert_service=alert_service, settings=settings)
        if closed:
            logger.info("Closed %d position(s) this cycle", closed)

        # Periodic re-scan (skip in fill-test mode)
        if (
            _uni_mode != "off"
            and not _fill_test_mode
            and _last_scan_at is not None
            and (now - _last_scan_at).total_seconds() / 60 >= _scan_interval_min
            and now < eod_time
        ):
            logger.info("Periodic universe re-scan …")
            try:
                rescanned = await _run_universe_scan(
                    settings=settings,
                    broker=broker,
                    journal=journal,
                    session_date=today_str,
                    scan_store=_scan_store,
                )
                if rescanned is None:
                    logger.warning(
                        "STANDBY: periodic re-scan rejected all candidates — "
                        "clearing active symbols"
                    )
                    active_symbols = []
                elif rescanned:
                    active_symbols = rescanned
                    logger.info("Re-scan updated active symbols: %s", active_symbols)
                else:
                    logger.warning(
                        "Re-scan returned no confirmed symbols — falling back to %s",
                        args.symbols,
                    )
                    active_symbols = list(args.symbols)
            except Exception as exc:
                logger.warning("Periodic re-scan failed: %s", exc)
            _scan_store["active_symbols"] = list(active_symbols)
            _last_scan_at = now

        # Only open new positions if not past EOD
        if now < eod_time:
            # Guard: block all new entries when a reconciliation mismatch is unresolved
            if _recon_has_mismatch:
                logger.info(
                    "Recon mismatch active — all new entries blocked until next clean reconcile"
                )
            for symbol in (active_symbols if not _recon_has_mismatch else []):
                # Global max-positions gate
                if len(pm.open_positions()) >= _max_active_pos:
                    logger.debug(
                        "Max active positions (%d) reached — skipping scan", _max_active_pos
                    )
                    break
                # Per-day symbol limit
                if symbol in symbols_traded_today:
                    logger.debug("Already traded %s today — skipping", symbol)
                    continue
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
                    store=store,
                    session_date=today_str,
                    alert_service=alert_service,
                )
                if placed > 0:
                    symbols_traded_today.add(symbol)
                    if len(symbols_traded_today) >= _max_sym_per_day:
                        logger.info(
                            "Max symbols traded per day (%d) reached", _max_sym_per_day
                        )
                        break
                session_placed += placed

        if _shutdown_requested:
            break

        await asyncio.sleep(args.poll)

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    now = datetime.now(tz=ET)
    logger.info(
        "Shutdown: %d position(s) open, %d order(s) pending",
        len(pm.open_positions()), fill_tracker.count(),
    )

    # 1. One final fill poll so we don't lose fills that arrived at shutdown
    if fill_tracker.count() > 0:
        logger.info("Shutdown: final fill poll for %d pending order(s)", fill_tracker.count())
        try:
            await fill_tracker.poll(broker, pm, journal, now, risk)
        except Exception as exc:
            logger.warning("Shutdown: final fill poll failed: %s", exc)

    # 2. Cancel open orders if requested
    if getattr(args, "cancel_pending", False) and fill_tracker.count() > 0:
        logger.warning(
            "Shutdown: cancelling %d pending order(s)", fill_tracker.count()
        )
        for pending in list(fill_tracker.pending_orders()):
            try:
                await broker.cancel_order(pending.order_id)
                logger.info("Shutdown: cancelled %s", pending.order_id[:8])
            except Exception as exc:
                logger.warning(
                    "Shutdown: cancel failed for %s: %s", pending.order_id[:8], exc
                )

    # 3. Liquidate any remaining open positions
    if pm.open_positions():
        logger.warning(
            "Shutdown: liquidating %d position(s)", len(pm.open_positions())
        )
        await eod_liquidate(broker, pm, journal, risk, now, args.dry_run, settings=settings)

    # 4. Generate and persist health report
    if journal and store:
        try:
            reporter = HealthReporter(db_session)
            report = await reporter.generate(
                session_date=today_str,
                api_errors=api_errors,
                reconciliation_warnings=recon_warnings,
            )
            import json as _json
            report_line = _json.dumps(report, default=str)
            logger.info("Health report: %s", report_line)

            # Write to file
            import os
            os.makedirs("logs", exist_ok=True)
            report_path = f"logs/session_{today_str}.json"
            with open(report_path, "w") as fh:
                _json.dump(report, fh, indent=2, default=str)
            logger.info("Health report written to %s", report_path)

            # Log to DB
            await journal.log_event(
                event="session_summary",
                message=(
                    f"Session complete: "
                    f"{report['trades']['total_closed']} trades, "
                    f"pnl={report['realized_pnl']}"
                ),
                data=report,
            )
            await journal.commit()

            await alert_service.send(
                AlertEvent.SESSION_SUMMARY,
                (
                    f"{report['trades']['total_closed']} trades | "
                    f"pnl={report['realized_pnl']} | "
                    f"win_rate={report['trades'].get('win_rate', 0):.0%}"
                ),
                data={
                    "trades": report["trades"]["total_closed"],
                    "realized_pnl": report["realized_pnl"],
                    "win_rate": report["trades"].get("win_rate"),
                    "max_drawdown": report.get("max_drawdown"),
                },
            )
        except Exception as exc:
            logger.error("Health report generation failed: %s", exc)

    # ── Post-session evaluation (evaluation mode) ─────────────────────────────
    if settings.paper_evaluation_mode:
        try:
            from app.evaluation.post_session import run_post_session
            post_result = await run_post_session(
                settings=settings,
                broker=broker,
                db_session=db_session,
                fill_tracker=fill_tracker,
                pm=pm,
                journal=journal,
                alert_service=alert_service,
                session_date=today_str,
            )
            logger.info(
                "Post-session evaluation | report=%s | ledger_updated=%s | errors=%s",
                post_result.report_path_json,
                post_result.ledger_updated,
                post_result.errors or "none",
            )
        except Exception as exc:
            logger.error("Post-session evaluation failed: %s", exc)

    logger.info(
        "Session complete | cycles=%d | placed=%d | pnl=%.2f",
        cycle, session_placed, float(risk.daily_pnl),
    )
    print(f"\n  Session complete — {cycle} cycles | {session_placed} orders | P&L ${float(risk.daily_pnl):.2f}\n")

    await alert_service.send(
        AlertEvent.SESSION_STOPPED,
        f"Session stopped | cycles={cycle} | placed={session_placed} | pnl={float(risk.daily_pnl):.2f}",
        data={"cycles": cycle, "placed": session_placed, "pnl": float(risk.daily_pnl), "api_errors": api_errors},
    )

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
    p.add_argument(
        "--cancel-pending", action="store_true",
        help="Cancel all pending orders on shutdown (default: leave open)",
    )
    p.add_argument(
        "--reconcile-interval", type=int, default=30, metavar="MINUTES",
        help="Broker reconciliation interval in minutes (default: 30)",
    )
    p.add_argument(
        "--eval", action="store_true",
        help="Enable paper evaluation mode (pre/post checklists, daily report, ledger)",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    # --eval flag sets the env var before settings are loaded
    if args.eval:
        os.environ.setdefault("PAPER_EVALUATION_MODE", "true")

    # Set up rotating JSON-capable logs before anything else
    from app.utils.logging_setup import configure_logging
    configure_logging(level=args.log_level)

    asyncio.run(run_session(args))
