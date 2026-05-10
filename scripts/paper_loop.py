"""
Manual paper-trading loop for SPY (or any ticker) options.

Runs one complete cycle:
  1. (Optional) Cancels unfilled orders older than --cancel-stale minutes.
  2. Fetches 5-minute intraday bars from yfinance (research only — not for
     execution pricing).
  3. Runs ORB, VWAP, and RSI strategies.
  4. Applies IV-crush filter.
  5. For each actionable signal:
       - Fetches a live Alpaca option chain (real bid/ask from data.alpaca.markets).
       - Selects the most liquid near-ATM contract via the liquidity filter.
       - Runs all pre-trade risk checks (session buffer, daily limits, etc.).
       - DRY RUN  : logs every decision; places no orders.
       - PAPER    : places a limit order on the Alpaca paper account.
  6. Prints a structured summary.

Usage:
  # Inspect what would be traded today — no orders placed:
  python scripts/paper_loop.py --dry-run

  # Place Alpaca paper limit orders:
  python scripts/paper_loop.py

  # Cancel unfilled orders older than 15 minutes, then run normally:
  python scripts/paper_loop.py --cancel-stale 15

  # Cancel stale orders only (no new signal scan):
  python scripts/paper_loop.py --cancel-stale 15 --dry-run

  # Different symbol:
  python scripts/paper_loop.py --symbol QQQ

  # Verbose internal logs:
  python scripts/paper_loop.py --dry-run --log-level INFO

Hard rules:
  - LIVE_TRADING_ENABLED must be false.  Script aborts otherwise.
  - All orders are limit orders — no market orders ever.
  - Kill switch file (./KILL_SWITCH) is respected before any order placement.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
import warnings
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent.parent))

ET = ZoneInfo("America/New_York")

# ── Console helpers ───────────────────────────────────────────────────────────

def _header(title: str):
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print(f"{'═' * 60}")


def _section(title: str):
    pad = max(0, 48 - len(title))
    print(f"\n── {title} {'─' * pad}")


def _ok(msg: str):
    print(f"  ✓ {msg}")


def _info(msg: str):
    print(f"  · {msg}")


def _warn(msg: str):
    print(f"  ⚠ {msg}", flush=True)


def _skip(msg: str):
    print(f"  ✗ {msg}")


# ── Stale-order cancellation ──────────────────────────────────────────────────

async def cancel_stale_orders(broker, max_age_minutes: int) -> int:
    """
    Cancel unfilled Alpaca orders older than max_age_minutes.
    Returns the number of orders cancelled.
    """
    _section(f"Cancelling stale orders (> {max_age_minutes} min unfilled)")
    try:
        orders = await broker.get_orders()
    except NotImplementedError:
        _info("Broker does not support get_orders — skipping stale cancellation")
        return 0
    except Exception as exc:
        _warn(f"Could not fetch open orders: {exc}")
        return 0

    if not orders:
        _info("No open orders found")
        return 0

    now = datetime.now(tz=ET)
    cutoff = now - timedelta(minutes=max_age_minutes)
    cancelled = 0

    for order in orders:
        if order.submitted_at is None:
            continue
        submitted = order.submitted_at
        if submitted.tzinfo is None:
            import datetime as _dt
            submitted = submitted.replace(tzinfo=_dt.timezone.utc)
        age_minutes = (now - submitted.astimezone(ET)).total_seconds() / 60
        sym = order.option_symbol or order.order_id[:8]

        if age_minutes > max_age_minutes:
            ok = await broker.cancel_order(order.order_id)
            if ok:
                _ok(f"Cancelled {sym}  (age {age_minutes:.0f} min | {order.status.value})")
                cancelled += 1
            else:
                _info(f"Could not cancel {sym} — may already be filled or cancelled")
        else:
            _info(f"Keeping {sym}  (age {age_minutes:.0f} min — within limit)")

    if cancelled == 0:
        _info(f"No orders exceeded the {max_age_minutes}-min threshold")
    else:
        _ok(f"Cancelled {cancelled} stale order(s)")
    return cancelled


# ── Signal processing ─────────────────────────────────────────────────────────

async def process_signal(
    sig,
    broker,
    risk,
    liq_filter,
    settings,
    now: datetime,
    dry_run: bool,
) -> bool:
    """
    Select a contract, run risk checks, and place (or log) a limit order.
    Returns True if an order was placed (or would be placed in dry-run mode).
    """
    from app.brokers.broker_interface import OrderRequest, OrderSide, OrderType
    from app.strategies.strategy_base import SignalDirection

    # ── Choose expiration ─────────────────────────────────────────────────────
    try:
        expirations = await broker.get_available_expirations(sig.symbol)
    except Exception as exc:
        _warn(f"  Cannot fetch expirations: {exc}")
        return False

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
        _warn(f"  No option expirations available for {sig.symbol}")
        return False

    dte_days = (target_exp - today).days
    _info(f"  Expiration : {target_exp} ({dte_days}DTE)")

    # ── Fetch option chain ────────────────────────────────────────────────────
    _info(f"  Fetching {sig.symbol} option chain from Alpaca ...")
    try:
        chain = await broker.get_option_chain(sig.symbol, target_exp)
    except Exception as exc:
        _warn(f"  Chain fetch failed: {exc}")
        return False

    calls_with_bid = sum(1 for c in chain.calls if c.bid > Decimal("0"))
    puts_with_bid  = sum(1 for c in chain.puts  if c.bid > Decimal("0"))
    _info(f"  Chain      : {len(chain.calls)} calls ({calls_with_bid} quoted), "
          f"{len(chain.puts)} puts ({puts_with_bid} quoted)")

    # ── Liquidity filter ──────────────────────────────────────────────────────
    contract = liq_filter.select_contract(chain, sig)
    if contract is None:
        _skip("  Liquidity filter: no qualifying contract")
        return False

    _info(f"  Contract   : {contract.option_symbol}")
    _info(f"  Bid/Ask    : ${contract.bid} / ${contract.ask}")
    _info(f"  OI / Vol   : {contract.open_interest:,} / {contract.volume:,}")
    _info(f"  Spread     : {contract.spread_pct:.1%}")
    if contract.delta is not None:
        _info(f"  Delta      : {contract.delta:.3f}")

    # ── Build order ───────────────────────────────────────────────────────────
    side = (
        OrderSide.BUY_TO_OPEN
        if sig.direction in (SignalDirection.LONG, SignalDirection.SHORT)
        else OrderSide.SELL_TO_CLOSE
    )
    offset = Decimal(str(settings.options.limit_price_offset_pct))
    limit_price = (contract.ask * (1 + offset)).quantize(Decimal("0.01"))

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

    # ── Risk check ────────────────────────────────────────────────────────────
    acct = await broker.get_account()
    risk_result = risk.check_order(
        request=request,
        equity=acct.equity,
        contract=contract,
        now=now,
    )

    if not risk_result.passed:
        _skip(f"  Risk check : FAILED")
        for msg in risk_result.messages:
            _info(f"               → {msg}")
        return False

    _ok(f"  Risk check : PASSED | qty={risk_result.approved_quantity} "
        f"| cost=${risk_result.approved_risk_dollars:.2f}")
    request.quantity = risk_result.approved_quantity

    # ── Place or log ──────────────────────────────────────────────────────────
    if dry_run:
        _info(f"  DRY RUN    : would place {side.value} {contract.option_symbol} "
              f"limit @ ${limit_price}")
        return True

    try:
        order = await broker.place_option_order(request)
        risk.record_trade()
        _ok(f"  Order      : {order.order_id[:8]}... | "
            f"status={order.status.value} | limit=${limit_price}")
        return True
    except Exception as exc:
        _warn(f"  Order placement failed: {exc}")
        return False


# ── Main loop ─────────────────────────────────────────────────────────────────

async def run(args: argparse.Namespace):
    from app.brokers import get_broker
    from app.config import get_settings
    from app.data import YFinanceDataSource
    from app.risk import RiskManager
    from app.strategies import (
        IVCrushFilter,
        LiquidityFilter,
        OpeningRangeBreakoutStrategy,
        RSITrendStrategy,
        VWAPReclaimStrategy,
    )

    settings = get_settings()
    t0 = time.monotonic()

    # ── Safety guard ──────────────────────────────────────────────────────────
    if settings.live_trading_enabled:
        print("ERROR: LIVE_TRADING_ENABLED=true. This script is for paper trading only.")
        sys.exit(1)

    mode = "DRY RUN  (no orders will be placed)" if args.dry_run else "PAPER"
    now = datetime.now(tz=ET)

    _header(
        f"SPY Options Paper Loop  ·  {now.strftime('%Y-%m-%d %H:%M:%S')} ET\n"
        f"  Mode: {mode}"
    )

    broker  = get_broker(settings)
    data    = YFinanceDataSource()
    risk    = RiskManager(settings)

    iv_filter = IVCrushFilter({
        "earnings_blackout_days": settings.risk.earnings_blackout_days,
        "allow_earnings_trades":  settings.risk.allow_earnings_trades,
    })
    liq_filter = LiquidityFilter({
        "min_open_interest": settings.risk.min_open_interest,
        "min_volume":        settings.risk.min_volume,
        "max_spread_pct":    settings.risk.max_spread_pct,
        "delta_target_min":  settings.options.delta_target_min,
        "delta_target_max":  settings.options.delta_target_max,
    })
    strategies = [
        OpeningRangeBreakoutStrategy(params={
            "range_minutes":       15,
            "min_range_pts":       0.5,
            "volume_confirmation": True,
        }),
        VWAPReclaimStrategy(params={
            "proximity_pct":     0.002,
            "confirmation_bars": 2,
        }),
        RSITrendStrategy(params={
            "rsi_period":        14,
            "rsi_oversold":      35,
            "trend_ema_period":  20,
        }),
    ]

    # ── Kill switch ───────────────────────────────────────────────────────────
    if settings.is_kill_switch_active():
        print(f"\n⛔  KILL SWITCH ACTIVE ({settings.kill_switch_file}) — aborting.")
        await broker.close()
        return

    # ── Account ───────────────────────────────────────────────────────────────
    _section("Account")
    acct = await broker.get_account()
    risk.start_session(acct.equity)
    _info(f"ID           : {acct.account_id}")
    _info(f"Equity       : ${acct.equity:,.2f}")
    _info(f"Cash         : ${acct.cash:,.2f}")
    _info(f"Paper mode   : {acct.is_paper}")
    _info(f"Risk session : {risk.trades_today} trades today | P&L ${risk.daily_pnl:.2f}")

    # ── Cancel stale orders ───────────────────────────────────────────────────
    if args.cancel_stale > 0:
        await cancel_stale_orders(broker, args.cancel_stale)

    # ── Market hours check ────────────────────────────────────────────────────
    open_h,  open_m  = map(int, settings.market_open.split(":"))
    close_h, close_m = map(int, settings.market_close.split(":"))
    mkt_open  = now.replace(hour=open_h,  minute=open_m,  second=0, microsecond=0)
    mkt_close = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)

    if not (mkt_open <= now <= mkt_close):
        _section("Market hours check")
        _warn(f"Outside market hours ({now.strftime('%H:%M')} ET) — "
              f"skipping signal scan (market {mkt_open.strftime('%H:%M')}–"
              f"{mkt_close.strftime('%H:%M')} ET)")
        await broker.close()
        _print_summary(0, 0, args.dry_run, time.monotonic() - t0)
        return

    # ── Process each symbol ───────────────────────────────────────────────────
    total_signals   = 0
    total_placed    = 0

    for symbol in args.symbols:
        # ── Fetch intraday bars ───────────────────────────────────────────────
        _section(f"Fetching 5-min {symbol} bars (yfinance — research only)")
        bars = await data.get_intraday_bars(symbol, interval="5m", days_back=3)
        if bars.empty:
            _warn(f"No bars returned for {symbol}")
            continue
        first = bars.index[0].tz_convert(ET).strftime("%Y-%m-%d %H:%M")
        last  = bars.index[-1].tz_convert(ET).strftime("%Y-%m-%d %H:%M")
        _info(f"Got {len(bars)} bars | {first} → {last} ET")

        # ── Run strategies ────────────────────────────────────────────────────
        _section(f"Strategies  ({symbol})")
        all_signals: List = []
        for strat in strategies:
            sigs = strat.generate_signals(bars, symbol)
            all_signals.extend(sigs)
            _info(f"{strat.strategy_id:<22} → {len(sigs):3d} signals")
        _info(f"{'Total':<22}    {len(all_signals):3d} signals")

        # ── IV crush filter ───────────────────────────────────────────────────
        _section("IV crush filter")
        filtered = iv_filter.apply(all_signals)
        _info(f"{len(filtered)} / {len(all_signals)} signals passed")
        actionable = [s for s in filtered if s.is_actionable()]

        if not actionable:
            _info("No actionable signals — nothing to process")
            continue

        # ── Process each signal ───────────────────────────────────────────────
        for i, sig in enumerate(actionable, 1):
            _section(f"Signal {i}/{len(actionable)}")
            _info(f"Strategy  : {sig.strategy_id}")
            _info(f"Direction : {sig.direction.value.upper()}")
            _info(f"Price     : ${sig.price:.2f}")
            _info(f"Notes     : {sig.notes or '—'}")
            _info(f"Time      : {sig.timestamp.strftime('%Y-%m-%d %H:%M %Z')}")

            placed = await process_signal(
                sig, broker, risk, liq_filter, settings, now, args.dry_run
            )
            total_signals += 1
            if placed:
                total_placed += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    await broker.close()
    _print_summary(total_signals, total_placed, args.dry_run, time.monotonic() - t0)


def _print_summary(signals: int, placed: int, dry_run: bool, elapsed: float):
    _section("Summary")
    _info(f"Signals processed : {signals}")
    if dry_run:
        _info(f"Orders placed     : 0  (dry run — none submitted)")
    else:
        _info(f"Orders placed     : {placed}")
    _info(f"Elapsed           : {elapsed:.1f}s")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Manual SPY options paper-trading loop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Run full pipeline but place no orders.",
    )
    p.add_argument(
        "--symbol", "--symbols",
        nargs="+",
        dest="symbols",
        default=["SPY"],
        metavar="SYM",
        help="Ticker(s) to process (default: SPY).",
    )
    p.add_argument(
        "--cancel-stale",
        type=int,
        default=0,
        metavar="MINUTES",
        help="Cancel unfilled orders older than MINUTES before scanning (0 = skip).",
    )
    p.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Internal library log level (default: WARNING).",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run(args))
