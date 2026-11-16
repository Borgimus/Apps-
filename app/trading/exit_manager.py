"""Exit management for open broker positions.

Moved verbatim from scripts/session_runner.py so exit behaviour can be read,
tested and reused without the session loop. The push notifier and the
fetch-failure recorder are passed in rather than read from runner globals.
Logs keep the "session_runner" logger name so existing log routing and
searches still find them.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from app.trading.retry import retry_async

ET = ZoneInfo("America/New_York")
logger = logging.getLogger("session_runner")

MANDATORY_EXIT_REASONS = frozenset({
    "stop_loss", "max_hold", "eod_exit", "daily_loss", "kill_switch", "quote_unavailable",
})

# An open position that cannot be priced cannot be protected by its stop.
# After this long without a usable quote it is closed as a mandatory exit.
MAX_UNPRICED_EXPOSURE_SECONDS = 120



async def place_exit_order(
    pos,
    broker,
    pm,
    now: datetime,
    reason: str,
    is_mandatory: bool,
    current_bid: Optional[float] = None,
    journal=None,
) -> None:
    """Fetch bid if needed, place a sell-to-close limit, and mark EXIT_PENDING."""
    from app.brokers.broker_interface import (
        OrderRequest as _OReq,
        OrderSide as _OSide,
        OrderType as _OType,
    )
    if current_bid is None:
        try:
            quote = await retry_async(
                lambda p=pos: broker.get_option_quote(p.option_symbol),
                label=f"exit_quote({pos.option_symbol})",
            )
            current_bid = float(quote.bid) if float(quote.bid) > 0 else None
        except Exception as exc:
            logger.warning("Exit order for %s priced from last mark: quote unavailable: %s",
                           pos.option_symbol, exc)
            current_bid = None

    exit_quote_bid = current_bid
    _remaining = pos.quantity - pos.confirmed_fill_qty
    if _remaining <= 0:
        logger.warning(
            "_place_exit_order: no remaining quantity for %s "
            "(qty=%d confirmed=%d) — skipping placement",
            pos.option_symbol, pos.quantity, pos.confirmed_fill_qty,
        )
        return
    fallback = pos.current_price if pos.current_price > 0 else pos.entry_price
    _lp = Decimal(str(round(
        current_bid if (current_bid and current_bid > 0) else fallback * 0.98, 2
    )))
    _req = _OReq(
        symbol=pos.symbol,
        option_symbol=pos.option_symbol,
        side=_OSide.SELL_TO_CLOSE,
        quantity=_remaining,
        order_type=_OType.LIMIT,
        limit_price=_lp,
        strategy_id=pos.strategy_id,
        notes=f"exit:{reason}",
    )
    try:
        order = await retry_async(
            lambda: broker.place_option_order(_req),
            label=f"exit_order({pos.option_symbol})",
        )
        pm.mark_exit_pending(
            pos.option_symbol,
            order_id=order.order_id,
            limit_price=float(_lp),
            reason=reason,
            is_mandatory=is_mandatory,
            exit_quote_bid=exit_quote_bid,
            now=now,
        )
        if journal is not None and pos.journal_id:
            await journal.mark_exit_pending(pos.journal_id, order.order_id, float(_lp))
            await journal.commit()
        logger.info(
            "Exit order placed | %s | limit=%.4f | order_id=%s | reason=%s",
            pos.option_symbol, float(_lp), order.order_id[:8], reason,
        )
    except Exception as exc:
        logger.error(
            "Exit order placement failed for %s: %s — position remains open (retry next cycle)",
            pos.option_symbol, exc,
        )


async def poll_pending_exit(
    pos,
    broker,
    pm,
    journal,
    risk,
    now: datetime,
    alert_service=None,
    *,
    notifier=None,
) -> bool:
    """
    Service one EXIT_PENDING position: check broker fill, close on confirmation,
    reprice on material bid move.  Returns True if position was fully closed.
    """
    from app.brokers.broker_interface import OrderStatus as _OStatus

    _terminal = (_OStatus.CANCELLED, _OStatus.CANCELED, _OStatus.REJECTED, _OStatus.EXPIRED)

    # ── Fetch order status ──────────────────────────────────────────────────
    try:
        order = await retry_async(
            lambda oid=pos.exit_order_id: broker.get_order_status(oid),
            label=f"order_status({pos.exit_order_id[:8] if pos.exit_order_id else '?'})",
        )
    except Exception as exc:
        logger.warning("Cannot fetch exit order status for %s: %s", pos.option_symbol, exc)
        return False

    status = order.status
    broker_qty = order.filled_quantity
    broker_price = float(order.filled_price) if order.filled_price else 0.0

    # Accumulate any new partial fills before evaluating status
    if broker_qty > pos.confirmed_fill_qty and broker_price > 0:
        pm.record_partial_fill(pos.option_symbol, broker_qty - pos.confirmed_fill_qty, broker_price)
        pos = pm.get_position(pos.option_symbol)
        if pos is None:
            return True

    # ── Fully filled ────────────────────────────────────────────────────────
    # The third clause handles the fill-cancel race: the broker cancelled the order
    # but a fill had already arrived at the exchange, so confirmed_fill_qty equals
    # the full position size even though status is CANCELLED.
    fully_filled = (
        status == _OStatus.FILLED
        or (status == _OStatus.PARTIALLY_FILLED and broker_qty >= pos.quantity)
        or pos.confirmed_fill_qty >= pos.quantity
    )
    if fully_filled:
        avg_fill = (
            pos.confirmed_fill_value / pos.confirmed_fill_qty
            if pos.confirmed_fill_qty > 0
            else broker_price
        )
        pnl = (avg_fill - pos.entry_price) * 100 * pos.quantity
        hold_secs = (now - pos.entry_time).total_seconds()
        pm.close_confirmed(pos.option_symbol, avg_fill, pnl)
        risk.record_exit(
            Decimal(str(pnl)),
            symbol=pos.symbol,
            direction=pos.direction,
            reason=pos.exit_triggered_reason,
        )
        if notifier:
            notifier.on_exit(
                symbol=pos.symbol,
                contract=pos.option_symbol,
                exit_price=avg_fill,
                pnl=pnl,
                reason=pos.exit_triggered_reason or "unknown",
                hold_secs=hold_secs,
                now=now,
            )
        if pos.journal_id and journal:
            _mfe = round((pos.peak_price - pos.entry_price) * 100 * pos.quantity, 2)
            _mae = round((pos.trough_price - pos.entry_price) * 100 * pos.quantity, 2)
            await journal.record_exit(
                journal_id=pos.journal_id,
                exit_time=order.filled_at or now,
                exit_price=avg_fill,
                exit_reason=pos.exit_triggered_reason,
                realized_pnl=pnl,
                hold_duration_secs=hold_secs,
                filled_quantity=pos.quantity,
                exit_bid=pos.exit_quote_bid,
                peak_price=pos.peak_price,
                trough_price=pos.trough_price,
                mfe=_mfe,
                mae=_mae,
                exit_order_id=pos.exit_order_id,
                exit_quote_bid=pos.exit_quote_bid,
            )
            await journal.log_event(
                event="exit",
                message=f"{pos.option_symbol} closed: {pos.exit_triggered_reason} pnl={pnl:.2f}",
                level="info",
                symbol=pos.symbol,
                data={
                    "reason": pos.exit_triggered_reason,
                    "pnl": round(pnl, 2),
                    "hold_secs": round(hold_secs),
                    "fill_price": avg_fill,
                    "exit_order_id": pos.exit_order_id,
                },
            )
            await journal.commit()
        if alert_service:
            from app.utils.alerting import AlertEvent
            _r = pos.exit_triggered_reason
            _aevent = (
                AlertEvent.STOP_LOSS if _r == "stop_loss" else
                AlertEvent.TAKE_PROFIT if _r in ("take_profit", "trailing_stop") else
                None
            )
            if _aevent:
                await alert_service.send(
                    _aevent,
                    f"{pos.option_symbol} {_r} pnl={pnl:.2f}",
                    data={"symbol": pos.symbol, "reason": _r, "pnl": round(pnl, 2)},
                )
        return True

    # ── Terminal: cancelled / rejected / expired ────────────────────────────
    if status in _terminal:
        reason = pos.exit_triggered_reason
        is_mandatory = pos.exit_is_mandatory
        logger.warning(
            "Exit order %s for %s terminal status: %s",
            pos.exit_order_id[:8] if pos.exit_order_id else "?",
            pos.option_symbol, status,
        )
        pm.clear_exit_pending(pos.option_symbol)
        if is_mandatory:
            await place_exit_order(pos, broker, pm, now, reason, is_mandatory, journal=journal)
        # Non-mandatory: Phase 2 will re-evaluate on next cycle
        return False

    # ── Still open: check for material bid move (bid < limit − $0.01) ───────
    try:
        quote = await retry_async(
            lambda p=pos: broker.get_option_quote(p.option_symbol),
            label=f"exit_reprice_quote({pos.option_symbol})",
        )
        current_bid = float(quote.bid) if float(quote.bid) > 0 else 0.0
    except Exception as exc:
        logger.warning("Exit reprice skipped for %s: quote unavailable: %s", pos.option_symbol, exc)
        return False

    if current_bid <= 0 or current_bid >= pos.exit_order_limit_price - 0.01:
        return False  # order still competitive

    logger.info(
        "Material bid move | %s | order=%s | limit=%.4f → bid=%.4f",
        pos.option_symbol, pos.exit_order_id[:8] if pos.exit_order_id else "?",
        pos.exit_order_limit_price, current_bid,
    )
    try:
        await broker.cancel_order(pos.exit_order_id)
    except Exception as exc:
        logger.warning("Cancel stale exit for %s failed: %s", pos.option_symbol, exc)
        return False

    # Confirm cancellation before re-placing
    try:
        cancelled = await retry_async(
            lambda oid=pos.exit_order_id: broker.get_order_status(oid),
            label=f"cancel_confirm({pos.exit_order_id[:8] if pos.exit_order_id else '?'})",
        )
        if cancelled.status not in (_OStatus.CANCELLED, _OStatus.CANCELED):
            logger.warning(
                "Cancel unconfirmed for %s (status=%s) — leaving as-is",
                pos.option_symbol, cancelled.status,
            )
            return False
    except Exception as exc:
        logger.warning("Cannot confirm cancel for %s: %s — leaving as-is", pos.option_symbol, exc)
        return False

    # Reconcile partial fills from the now-cancelled exit order before re-placing.
    # A fill-cancel race leaves status=CANCELLED but filled_quantity > 0; submitting
    # a replacement for the full original quantity would overshoot the position.
    _cfq = cancelled.filled_quantity or 0
    _cfp = float(cancelled.filled_price) if cancelled.filled_price else 0.0
    if _cfq > pos.confirmed_fill_qty and _cfp > 0:
        pm.record_partial_fill(pos.option_symbol, _cfq - pos.confirmed_fill_qty, _cfp)
        pos = pm.get_position(pos.option_symbol)
        if pos is None:
            return True
    if pos.confirmed_fill_qty >= pos.quantity:
        logger.info(
            "Fill-cancel race: %s confirmed_fill_qty=%d >= qty=%d "
            "— deferring close to next poll cycle (no replacement placed)",
            pos.option_symbol, pos.confirmed_fill_qty, pos.quantity,
        )
        return False  # EXIT_PENDING stays True; next poll sees fully_filled=True and closes

    reason = pos.exit_triggered_reason
    is_mandatory = pos.exit_is_mandatory
    pm.clear_exit_pending(pos.option_symbol)

    if is_mandatory:
        await place_exit_order(pos, broker, pm, now, reason, is_mandatory, current_bid=current_bid, journal=journal)
    else:
        # Re-evaluate condition at current price before re-placing
        current_price = float(quote.mid) if float(quote.mid) > 0 else current_bid
        pm.update_price(pos.option_symbol, current_price)
        new_reason = pm.should_exit(pos.option_symbol, current_price, now)
        if new_reason:
            await place_exit_order(
                pos, broker, pm, now, new_reason,
                new_reason in MANDATORY_EXIT_REASONS,
                current_bid=current_bid,
                journal=journal,
            )
        else:
            logger.info(
                "Exit condition %s no longer holds for %s after bid move — position stays open",
                reason, pos.option_symbol,
            )
    return False


async def monitor_positions(
    broker,
    pm,
    journal,
    risk,
    now: datetime,
    dry_run: bool,
    alert_service=None,
    settings=None,
    *,
    record_failure,
    notifier=None,
) -> int:
    """
    Phase 1: service EXIT_PENDING positions — poll broker fill status, close on
    confirmation, reprice on material bid move.
    Phase 2: evaluate exit conditions for non-pending positions — place exit
    orders but never close locally without broker confirmation.
    Returns number of positions fully closed this cycle.
    """
    closed = 0

    # ── Phase 1: service EXIT_PENDING positions ──────────────────────────────
    for pos in list(pm.open_positions()):
        if not pos.exit_pending or dry_run:
            continue
        was_closed = await poll_pending_exit(pos, broker, pm, journal, risk, now, alert_service,
                                              notifier=notifier)
        if was_closed:
            closed += 1

    # ── Phase 2: evaluate exit conditions for non-pending positions ──────────
    for pos in list(pm.open_positions()):
        if pos.exit_pending:
            continue

        _exit_bid: Optional[float] = None
        _exit_ask: Optional[float] = None
        _exit_mid: Optional[float] = None
        current_price: Optional[float] = None
        try:
            quote = await retry_async(
                lambda p=pos: broker.get_option_quote(p.option_symbol),
                label=f"get_option_quote({pos.option_symbol})",
            )
            _exit_bid = float(quote.bid)
            _exit_ask = float(quote.ask)
            _exit_mid = float(quote.mid) if float(quote.mid) > 0 else None
            # Validate quote age — warn if exchange timestamp is stale (>60 s).
            from app.trading.quote_evidence import parse_quote_timestamp
            _quote_ts = parse_quote_timestamp(quote.timestamp)
            _q_age = (now - _quote_ts).total_seconds() if _quote_ts else None
            if _q_age is None:
                logger.warning("Option quote timestamp unavailable: %s", pos.option_symbol)
            elif _q_age > 60 or _q_age < 0:
                logger.warning(
                    "Stale option quote: %s age=%.0fs", pos.option_symbol, _q_age
                )
            # Use bid for exit decisions on long options (reflects executable price).
            # Mid is used only for logging/display so phantom trailing-stop peaks are avoided.
            current_price = _exit_bid if _exit_bid and _exit_bid > 0 else _exit_mid
        except Exception as exc:
            logger.warning("Exit quote unavailable | %s | %s", pos.option_symbol, exc)

        if current_price:
            pos.unpriced_since = None
            pm.update_price(pos.option_symbol, current_price)
            reason = pm.should_exit(pos.option_symbol, current_price, now)
        else:
            # Never substitute a price: a fabricated mark at entry makes the
            # position look flat, which silently disables stop and trailing exits.
            record_failure(f"exit_quote({pos.option_symbol})")
            if pos.unpriced_since is None:
                pos.unpriced_since = now
            _unpriced_secs = (now - pos.unpriced_since).total_seconds()
            logger.warning(
                "UNPRICED EXPOSURE | %s | no usable quote for %.0fs",
                pos.option_symbol, _unpriced_secs,
            )
            # Time-based exits still apply against the last valid mark.
            current_price = pos.current_price
            reason = pm.should_exit(pos.option_symbol, current_price, now)
            if not reason and _unpriced_secs >= MAX_UNPRICED_EXPOSURE_SECONDS:
                reason = "quote_unavailable"

        if not reason:
            continue

        _exit_price_est = _exit_bid if (_exit_bid and _exit_bid > 0) else current_price
        logger.info(
            "Exit triggered | %s | reason=%s | bid=%.4f | pnl_est=%.2f",
            pos.option_symbol, reason, _exit_price_est,
            (_exit_price_est - pos.entry_price) * 100 * pos.quantity,
        )

        # Spread warning — never blocks exit
        if settings and _exit_bid and _exit_ask and _exit_ask > 0:
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
                            },
                        )
                        await journal.commit()

        if dry_run:
            _pnl_dry = (_exit_price_est - pos.entry_price) * 100 * pos.quantity
            logger.info("DRY RUN: would close %s (%s)", pos.option_symbol, reason)
            pm.close(pos.option_symbol, _exit_price_est, _pnl_dry)
            closed += 1
            continue

        # Place exit order; position stays open until broker confirms fill
        await place_exit_order(
            pos, broker, pm, now, reason,
            reason in MANDATORY_EXIT_REASONS,
            current_bid=_exit_bid,
            journal=journal,
        )

    return closed


async def eod_liquidate(broker, pm, journal, risk, now: datetime, dry_run: bool, settings=None, *, notifier=None):
    """
    Force-close every open position at end-of-day.

    Places exit orders and polls for up to 90 seconds for broker confirmation.
    Positions are not removed from local state until fills are confirmed.
    Any position still EXIT_PENDING after 90s remains open for post_session
    orphan-close recovery.
    """
    positions = pm.open_positions()
    if not positions:
        return
    logger.warning("EOD liquidation: %d position(s) to close", len(positions))

    if dry_run:
        for pos in list(positions):
            pnl = (pos.current_price - pos.entry_price) * 100 * pos.quantity
            pm.close_confirmed(pos.option_symbol, pos.current_price, pnl)
            logger.info("DRY RUN: EOD closed %s pnl=%.2f", pos.option_symbol, pnl)
        return

    from app.brokers.broker_interface import (
        OrderRequest as _OReq,
        OrderSide as _OSide,
        OrderType as _OType,
        OrderStatus as _OStatus,
    )
    _terminal = (_OStatus.CANCELLED, _OStatus.CANCELED, _OStatus.REJECTED, _OStatus.EXPIRED)

    # ── Phase 1: place EOD orders for positions not already EXIT_PENDING ─────
    for pos in list(positions):
        if pos.exit_pending:
            # Already has an exit order — upgrade to mandatory EOD
            pos.exit_is_mandatory = True
            pos.exit_triggered_reason = "eod_exit"
            logger.info(
                "EOD: %s already EXIT_PENDING (order=%s) — upgraded to mandatory",
                pos.option_symbol, pos.exit_order_id[:8] if pos.exit_order_id else "none",
            )
            continue

        try:
            quote = await retry_async(
                lambda p=pos: broker.get_option_quote(p.option_symbol),
                label=f"eod_quote({pos.option_symbol})",
            )
            _bid = float(quote.bid) if float(quote.bid) > 0 else None
            _ask = float(quote.ask) if float(quote.ask) > 0 else None
        except Exception as exc:
            logger.warning("EOD quote unavailable for %s: %s", pos.option_symbol, exc)
            _bid = None
            _ask = None

        if settings and _bid and _ask and _ask > 0:
            _mid = (_bid + _ask) / 2
            if _mid > 0:
                _sp = (_ask - _bid) / _mid
                _max_sp = getattr(settings.risk, "max_spread_pct", 0.10)
                if _sp > _max_sp:
                    logger.warning(
                        "EOD spread warning | %s | spread_pct=%.3f bid=%.4f ask=%.4f (proceeding)",
                        pos.option_symbol, _sp, _bid, _ask,
                    )
                    if journal:
                        await journal.log_event(
                            event="exit_spread_warning",
                            message=f"{pos.option_symbol} wide spread on EOD exit: spread_pct={_sp:.3f}",
                            level="warning",
                            symbol=pos.symbol,
                            data={"reason": "eod_exit", "spread_pct": round(_sp, 4), "bid": _bid, "ask": _ask},
                        )
                        await journal.commit()

        fallback = pos.current_price if pos.current_price > 0 else pos.entry_price
        _lp = Decimal(str(round(_bid if (_bid and _bid > 0) else fallback * 0.98, 2)))
        _req = _OReq(
            symbol=pos.symbol,
            option_symbol=pos.option_symbol,
            side=_OSide.SELL_TO_CLOSE,
            quantity=pos.quantity,
            order_type=_OType.LIMIT,
            limit_price=_lp,
            strategy_id=pos.strategy_id,
            notes="exit:eod_exit",
        )
        try:
            order = await retry_async(
                lambda: broker.place_option_order(_req),
                label=f"eod_exit_order({pos.option_symbol})",
            )
            pm.mark_exit_pending(pos.option_symbol, order.order_id, float(_lp), "eod_exit", True, _bid, now)
            if journal and pos.journal_id:
                await journal.mark_exit_pending(pos.journal_id, order.order_id, float(_lp))
                await journal.commit()
            logger.info(
                "EOD exit order placed | %s | limit=%.4f | order_id=%s",
                pos.option_symbol, float(_lp), order.order_id[:8],
            )
        except Exception as exc:
            logger.error(
                "EOD exit order failed for %s: %s — marking EXIT_PENDING for orphan recovery",
                pos.option_symbol, exc,
            )
            pm.mark_exit_pending(pos.option_symbol, "", float(_lp), "eod_exit", True, _bid, now)

    # ── Phase 2: 90-second polling loop ──────────────────────────────────────
    _deadline = now + timedelta(seconds=90)
    _poll_secs = 5

    while datetime.now(tz=ET) < _deadline:
        pending = [p for p in pm.open_positions() if p.exit_pending and p.exit_order_id]
        if not pending:
            break
        await asyncio.sleep(_poll_secs)
        poll_now = datetime.now(tz=ET)

        for pos in list(pending):
            try:
                order = await retry_async(
                    lambda oid=pos.exit_order_id: broker.get_order_status(oid),
                    label=f"eod_poll({pos.option_symbol})",
                )
            except Exception as exc:
                logger.warning("EOD: cannot poll %s: %s", pos.option_symbol, exc)
                continue

            status = order.status
            broker_qty = order.filled_quantity
            broker_price = float(order.filled_price) if order.filled_price else 0.0

            if broker_qty > pos.confirmed_fill_qty and broker_price > 0:
                pm.record_partial_fill(pos.option_symbol, broker_qty - pos.confirmed_fill_qty, broker_price)
                pos = pm.get_position(pos.option_symbol)
                if pos is None:
                    continue

            fully_filled = (
                status == _OStatus.FILLED
                or (status == _OStatus.PARTIALLY_FILLED and broker_qty >= pos.quantity)
                or pos.confirmed_fill_qty >= pos.quantity
            )
            if fully_filled:
                avg_fill = (
                    pos.confirmed_fill_value / pos.confirmed_fill_qty
                    if pos.confirmed_fill_qty > 0 else broker_price
                )
                pnl = (avg_fill - pos.entry_price) * 100 * pos.quantity
                hold_secs = (poll_now - pos.entry_time).total_seconds()
                pm.close_confirmed(pos.option_symbol, avg_fill, pnl)
                risk.record_exit(
                    Decimal(str(pnl)),
                    symbol=pos.symbol,
                    direction=pos.direction,
                    reason="eod_exit",
                )
                if notifier:
                    notifier.on_exit(
                        symbol=pos.symbol,
                        contract=pos.option_symbol,
                        exit_price=avg_fill,
                        pnl=pnl,
                        reason="eod_exit",
                        hold_secs=hold_secs,
                        now=poll_now,
                    )
                if pos.journal_id and journal:
                    _mfe = round((pos.peak_price - pos.entry_price) * 100 * pos.quantity, 2)
                    _mae = round((pos.trough_price - pos.entry_price) * 100 * pos.quantity, 2)
                    await journal.record_exit(
                        journal_id=pos.journal_id,
                        exit_time=order.filled_at or poll_now,
                        exit_price=avg_fill,
                        exit_reason="eod_exit",
                        realized_pnl=pnl,
                        hold_duration_secs=hold_secs,
                        filled_quantity=pos.quantity,
                        exit_bid=pos.exit_quote_bid,
                        peak_price=pos.peak_price,
                        trough_price=pos.trough_price,
                        mfe=_mfe,
                        mae=_mae,
                        exit_order_id=pos.exit_order_id,
                        exit_quote_bid=pos.exit_quote_bid,
                    )
                    await journal.commit()
                logger.info("EOD confirmed | %s | fill=%.4f | pnl=%.2f", pos.option_symbol, avg_fill, pnl)
                continue

            if status in _terminal:
                # Re-place immediately — EOD is always mandatory
                pm.clear_exit_pending(pos.option_symbol)
                try:
                    _q = await retry_async(lambda p=pos: broker.get_option_quote(p.option_symbol), label=f"eod_requeue_q({pos.option_symbol})")
                    _new_bid = float(_q.bid) if float(_q.bid) > 0 else None
                except Exception as exc:
                    logger.warning("EOD requeue quote unavailable for %s: %s", pos.option_symbol, exc)
                    _new_bid = None
                _rem = max(0, pos.quantity - pos.confirmed_fill_qty)
                if _rem == 0:
                    continue
                fallback2 = pos.current_price if pos.current_price > 0 else pos.entry_price
                _new_lp = Decimal(str(round(_new_bid if (_new_bid and _new_bid > 0) else fallback2 * 0.98, 2)))
                _rq = _OReq(
                    symbol=pos.symbol, option_symbol=pos.option_symbol,
                    side=_OSide.SELL_TO_CLOSE, quantity=_rem,
                    order_type=_OType.LIMIT, limit_price=_new_lp,
                    strategy_id=pos.strategy_id, notes="exit:eod_exit",
                )
                try:
                    _new_ord = await retry_async(lambda: broker.place_option_order(_rq), label=f"eod_requeue({pos.option_symbol})")
                    pm.mark_exit_pending(pos.option_symbol, _new_ord.order_id, float(_new_lp), "eod_exit", True, _new_bid, poll_now)
                    if journal and pos.journal_id:
                        await journal.mark_exit_pending(pos.journal_id, _new_ord.order_id, float(_new_lp))
                        await journal.commit()
                    logger.info("EOD re-queued | %s | limit=%.4f | order_id=%s", pos.option_symbol, float(_new_lp), _new_ord.order_id[:8])
                except Exception as exc:
                    logger.error("EOD re-queue failed for %s: %s", pos.option_symbol, exc)
                continue

            # Still open: reprice if bid moved materially
            try:
                _q = await retry_async(lambda p=pos: broker.get_option_quote(p.option_symbol), label=f"eod_reprice_q({pos.option_symbol})")
                _cur_bid = float(_q.bid) if float(_q.bid) > 0 else None
            except Exception as exc:
                logger.warning("EOD reprice quote unavailable for %s: %s", pos.option_symbol, exc)
                _cur_bid = None

            if _cur_bid and _cur_bid < pos.exit_order_limit_price - 0.01:
                try:
                    await broker.cancel_order(pos.exit_order_id)
                    # Confirm cancel before re-placing to prevent fill-cancel race
                    _cancel_conf = await broker.get_order_status(pos.exit_order_id)
                    if _cancel_conf.status not in (_OStatus.CANCELLED, _OStatus.CANCELED):
                        logger.warning(
                            "EOD reprice: cancel unconfirmed for %s (status=%s) — skipping replacement",
                            pos.option_symbol, _cancel_conf.status,
                        )
                        continue
                    # Reconcile partial fills from the cancelled order
                    _cfq = _cancel_conf.filled_quantity or 0
                    _cfp = float(_cancel_conf.filled_price) if _cancel_conf.filled_price else 0.0
                    if _cfq > pos.confirmed_fill_qty and _cfp > 0:
                        pm.record_partial_fill(
                            pos.option_symbol, _cfq - pos.confirmed_fill_qty, _cfp
                        )
                        pos = pm.get_position(pos.option_symbol)
                        if pos is None:
                            continue
                    if pos.confirmed_fill_qty >= pos.quantity:
                        logger.info(
                            "EOD reprice: fill-cancel race for %s — no replacement needed",
                            pos.option_symbol,
                        )
                        continue
                    _rem = max(0, pos.quantity - pos.confirmed_fill_qty)
                    if _rem == 0:
                        continue
                    _new_lp = Decimal(str(round(_cur_bid, 2)))
                    _rp = _OReq(
                        symbol=pos.symbol, option_symbol=pos.option_symbol,
                        side=_OSide.SELL_TO_CLOSE, quantity=_rem,
                        order_type=_OType.LIMIT, limit_price=_new_lp,
                        strategy_id=pos.strategy_id, notes="exit:eod_exit",
                    )
                    _rp_ord = await retry_async(lambda: broker.place_option_order(_rp), label=f"eod_reprice({pos.option_symbol})")
                    pm.mark_exit_pending(pos.option_symbol, _rp_ord.order_id, float(_new_lp), "eod_exit", True, _cur_bid, poll_now)
                    if journal and pos.journal_id:
                        await journal.mark_exit_pending(pos.journal_id, _rp_ord.order_id, float(_new_lp))
                        await journal.commit()
                    logger.info("EOD repriced | %s | %.4f → %.4f | order_id=%s", pos.option_symbol, pos.exit_order_limit_price, float(_new_lp), _rp_ord.order_id[:8])
                except Exception as exc:
                    logger.warning("EOD reprice for %s failed: %s", pos.option_symbol, exc)

    # ── Phase 3: log unresolved positions for post_session orphan recovery ────
    unresolved = [p for p in pm.open_positions() if p.exit_pending]
    if unresolved:
        logger.warning(
            "EOD: %d position(s) still EXIT_PENDING after 90s — post_session orphan recovery required",
            len(unresolved),
        )
        for pos in unresolved:
            logger.warning(
                "EOD unresolved | %s | order_id=%s | qty=%d | confirmed_qty=%d | reason=%s",
                pos.option_symbol, pos.exit_order_id or "none",
                pos.quantity, pos.confirmed_fill_qty, pos.exit_triggered_reason,
            )
