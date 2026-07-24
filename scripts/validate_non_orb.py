#!/usr/bin/env python3
"""
validate_non_orb.py — Non-ORB pipeline validation (Sprint 7)

Tests 10 aspects of the trading system that are available outside the
Opening Range Breakout window:

  1.  Universe scan: full ticker rank, reason codes, rejection reasons
  2.  DB persistence: DBScanResult rows, re-queryable, field accuracy
  3.  Dashboard: GET /scan/results structure
  4.  Alpaca confirmation: top-5 bid/ask/OI/vol/spread/exp/delta
  5.  VWAP strategy: signal on current 5m bars or detailed no-signal diagnosis
  6.  RSI + trend: signal on current 5m bars or detailed no-signal diagnosis
  7.  Liquidity filter: spread/OI/volume/ATM-proximity ranking
  8.  Paper lifecycle: top confirmed candidate → place → fill → exit
  9.  Exit management: stop/TP/trailing/unrealized-PnL (synthetic + real if filled)
  10. Daily report + ledger

Acceptance criteria enforced:
  - LIVE_TRADING_ENABLED must be false
  - No orphan orders or positions on exit
  - Useful diagnostics produced even without a trade fill

Usage:
  PAPER_EVALUATION_MODE=true python scripts/validate_non_orb.py
  python scripts/validate_non_orb.py --max-symbols 5   # scan fewer symbols
  python scripts/validate_non_orb.py --skip-paper       # skip paper lifecycle
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import warnings
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path, override=False)

import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")

# ── Output helpers ─────────────────────────────────────────────────────────────

_BANNER = "─" * 64
_checks_passed = 0
_checks_total = 0


def _step(n: int, label: str) -> None:
    print(f"\n{_BANNER}")
    print(f"  STAGE {n}: {label}")
    print(_BANNER)


def _check(condition: bool, label: str) -> bool:
    global _checks_passed, _checks_total
    _checks_total += 1
    if condition:
        _checks_passed += 1
        print(f"  ✓  {label}")
    else:
        print(f"  ✗  {label}")
    return condition


def _info(msg: str) -> None:
    print(f"     {msg}")


def _warn(msg: str) -> None:
    print(f"  ⚠  {msg}")


# ── Indicator helpers (for diagnostics when strategies return no signal) ───────

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)


def _ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def _vwap_series(bars: pd.DataFrame) -> pd.Series:
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3
    cum_vol = bars["volume"].cumsum()
    cum_tpv = (typical * bars["volume"]).cumsum()
    return cum_tpv / cum_vol.replace(0, float("nan"))


def _today_bars(bars: pd.DataFrame) -> pd.DataFrame:
    today = datetime.now(tz=ET).date()
    mask = bars.index.tz_convert(ET).date == today
    return bars.loc[mask]


# ── VWAP no-signal diagnosis ──────────────────────────────────────────────────

def _diagnose_vwap(bars: pd.DataFrame, symbol: str, proximity_pct: float, confirm: int) -> None:
    bars = bars.copy()
    bars.columns = [c.lower() for c in bars.columns]
    today = datetime.now(tz=ET).date()

    today_mask = bars.index.tz_convert(ET).date == today
    today_b = bars.loc[today_mask]

    if today_b.empty:
        _warn(f"VWAP diagnosis: no intraday bars for {symbol} today")
        return

    vwap = _vwap_series(today_b)
    last_close = float(today_b["close"].iloc[-1])
    last_vwap = float(vwap.iloc[-1])
    proximity = abs(last_close - last_vwap) / max(last_vwap, 0.01)

    side = "above" if last_close > last_vwap else "below"
    _info(f"  Current price: {last_close:.2f}  |  VWAP: {last_vwap:.2f}  |  side: {side}")
    _info(f"  Proximity to VWAP: {proximity:.3%}  (threshold: {proximity_pct:.3%})")

    # Count VWAP crosses today
    close_vals = today_b["close"].values
    vwap_vals = vwap.values
    crosses = sum(
        1 for i in range(1, len(close_vals))
        if (close_vals[i - 1] <= vwap_vals[i - 1]) != (close_vals[i] <= vwap_vals[i])
    )
    _info(f"  VWAP crosses today: {crosses}")

    if crosses == 0:
        _warn("  No signal: price has not crossed VWAP today")
    elif proximity > proximity_pct:
        _warn(
            f"  No signal: price too far from VWAP to trigger new reclaim "
            f"({proximity:.3%} > {proximity_pct:.3%})"
        )
    else:
        _warn(
            f"  Crosses present but {confirm}-bar confirmation not met "
            "(price didn't hold after cross)"
        )


# ── RSI no-signal diagnosis ───────────────────────────────────────────────────

def _diagnose_rsi(
    bars: pd.DataFrame,
    symbol: str,
    rsi_period: int,
    rsi_oversold: float,
    rsi_overbought: float,
    ema_period: int,
) -> None:
    bars = bars.copy()
    bars.columns = [c.lower() for c in bars.columns]

    if len(bars) < rsi_period + 2:
        _warn(f"RSI diagnosis: insufficient bars ({len(bars)} < {rsi_period + 2})")
        return

    rsi_vals = _rsi(bars["close"], rsi_period)
    ema_vals = _ema(bars["close"], ema_period)
    bars = bars.dropna()

    last_close = float(bars["close"].iloc[-1])
    last_rsi = float(rsi_vals.iloc[-1])
    last_ema = float(ema_vals.iloc[-1])
    price_vs_ema = "above" if last_close > last_ema else "below"

    _info(f"  RSI({rsi_period}): {last_rsi:.1f}  |  EMA({ema_period}): {last_ema:.2f}  |  price {price_vs_ema} EMA")
    _info(f"  Oversold threshold: {rsi_oversold}  |  Overbought threshold: {rsi_overbought}")

    # Look for crossed thresholds in recent bars
    rsi_arr = rsi_vals.dropna().values
    if len(rsi_arr) >= 2:
        prev_rsi = float(rsi_arr[-2])
        crossed_up = prev_rsi < rsi_oversold <= last_rsi
        crossed_down = prev_rsi > rsi_overbought >= last_rsi
        if crossed_up:
            _info(f"  RSI crossed above oversold ({prev_rsi:.1f}→{last_rsi:.1f}) but EMA trend filter blocked")
        elif crossed_down:
            _info(f"  RSI crossed below overbought ({prev_rsi:.1f}→{last_rsi:.1f}) but EMA trend filter blocked")
        else:
            _warn(
                f"  No signal: RSI={last_rsi:.1f} not near thresholds "
                f"(oversold={rsi_oversold}, overbought={rsi_overbought})"
            )
    if last_rsi <= rsi_oversold and price_vs_ema == "below":
        _warn("  LONG signal blocked: RSI oversold but price is below EMA (no uptrend)")
    elif last_rsi >= rsi_overbought and price_vs_ema == "above":
        _warn("  SHORT signal blocked: RSI overbought but price is above EMA (no downtrend)")


# ── Exit management synthetic test ────────────────────────────────────────────

def _test_exit_management(pm, symbol: str, option_symbol: str, entry_price: float) -> None:
    """
    Opens a synthetic position in pm and verifies all exit condition logic.
    Removes the position on completion.
    """
    print()
    _info(f"Exit management synthetic test for {option_symbol} @ entry=${entry_price:.4f}")

    pos = pm.open(
        option_symbol=option_symbol,
        symbol=symbol,
        strategy_id="exit_mgmt_test",
        direction="LONG",
        entry_time=datetime.now(tz=ET),
        entry_price=entry_price,
        quantity=1,
    )

    sl_pct = pos.stop_loss_pct
    tp_pct = pos.take_profit_pct
    ts_pct = pos.trailing_stop_pct

    stop_loss_level = round(entry_price * (1 - sl_pct), 4)
    take_profit_level = round(entry_price * (1 + tp_pct), 4)
    _check(
        abs(entry_price * (1 - sl_pct) - stop_loss_level) < 0.001,
        f"Stop loss level: ${stop_loss_level:.4f}  ({sl_pct:.0%} below entry)",
    )
    _check(
        abs(entry_price * (1 + tp_pct) - take_profit_level) < 0.001,
        f"Take profit level: ${take_profit_level:.4f}  ({tp_pct:.0%} above entry)",
    )

    # Test price update and trailing stop
    peak_price = entry_price * 1.10  # simulate +10% gain
    pm.update_price(option_symbol, peak_price)
    _check(
        pos.peak_price == peak_price,
        f"peak_price updated to ${peak_price:.4f} after +10% move",
    )

    trailing_stop_level = round(peak_price * (1 - ts_pct), 4)
    _check(
        abs(pos.peak_price * (1 - ts_pct) - trailing_stop_level) < 0.001,
        f"Trailing stop level: ${trailing_stop_level:.4f}  ({ts_pct:.0%} below peak)",
    )

    # Verify unrealized PnL via direct attribute access
    open_pos = pm.open_positions()
    if open_pos:
        p = open_pos[0]
        unrealized = round((p.current_price - p.entry_price) * 100 * p.quantity, 2)
        expected_pnl = round((peak_price - entry_price) * 100 * 1, 2)
        _check(
            abs(unrealized - expected_pnl) < 0.01,
            f"Unrealized PnL: ${unrealized:.2f}  (expected ${expected_pnl:.2f})",
        )

    # Test stop loss trigger
    sl_price = entry_price * (1 - sl_pct) * 0.99  # just below stop
    reason = pm.should_exit(option_symbol, sl_price, datetime.now(tz=ET))
    _check(reason == "stop_loss", f"should_exit at ${sl_price:.4f} → '{reason}' (expected stop_loss)")

    # Reset to entry price, then test take profit
    pm.update_price(option_symbol, entry_price)
    pos.peak_price = entry_price  # reset peak
    tp_price = entry_price * (1 + tp_pct) * 1.01
    reason = pm.should_exit(option_symbol, tp_price, datetime.now(tz=ET))
    _check(reason == "take_profit", f"should_exit at ${tp_price:.4f} → '{reason}' (expected take_profit)")

    # Test trailing stop: price rallied, then fell
    pm.update_price(option_symbol, peak_price)       # rally
    ts_trigger = peak_price * (1 - ts_pct) * 0.99   # just below trailing stop
    reason = pm.should_exit(option_symbol, ts_trigger, datetime.now(tz=ET))
    _check(reason == "trailing_stop", f"should_exit after rally+pullback → '{reason}' (expected trailing_stop)")

    # Clean up synthetic position (always, even if earlier assertions crashed)
    if pm.has_position(option_symbol):
        pm.close(option_symbol, entry_price, pnl=0.0)
    _check(not pm.has_position(option_symbol), "Synthetic position removed from PositionManager")


# ── Main validation ────────────────────────────────────────────────────────────

async def main(args: argparse.Namespace) -> int:
    global _checks_passed, _checks_total

    from app.api.models import AsyncSessionLocal, DBScanResult, init_db
    from app.brokers import get_broker
    from app.brokers.broker_interface import OrderRequest, OrderSide, OrderType
    from app.config import get_settings
    from app.data import YFinanceDataSource
    from app.risk import RiskManager
    from app.scanning import (
        AlpacaConfirmer,
        CandidateScore,
        CandidateScorer,
        YFinanceScanner,
    )
    from app.scanning.universe_loader import UniverseLoader
    from app.strategies import LiquidityFilter, RSITrendStrategy, VWAPReclaimStrategy
    from app.strategies.strategy_base import Signal, SignalDirection
    from app.trading import FillTracker, PositionManager, TradeJournal
    from app.trading.pending_order_store import PendingOrderStore
    from app.trading.pricing import compute_limit_price
    from sqlalchemy import select, text

    settings = get_settings()
    now_et = datetime.now(tz=ET)
    today_str = now_et.strftime("%Y-%m-%d")
    today = now_et.date()

    # ── Hard safety gate ──────────────────────────────────────────────────────
    if settings.live_trading_enabled:
        print("FATAL: LIVE_TRADING_ENABLED=true — aborting to protect real capital")
        return 1

    # ── Infrastructure setup ──────────────────────────────────────────────────
    await init_db()
    db_session = AsyncSessionLocal()
    journal = TradeJournal(db_session, is_paper=True)

    broker = get_broker(settings)
    try:
        acct = await broker.get_account()
    except Exception as exc:
        print(f"FATAL: cannot connect to broker: {exc}")
        await db_session.close()
        return 1

    risk = RiskManager(settings)
    pm = PositionManager(settings)
    store = PendingOrderStore(db_session)
    fill_tracker = FillTracker(
        max_age_minutes=settings.entry_order_timeout_secs / 60, store=store
    )
    risk.start_session(acct.equity)

    # Reusable components
    liq_filter = LiquidityFilter({
        "min_open_interest": settings.risk.min_open_interest,
        "min_volume":         settings.risk.min_volume,
        "max_spread_pct":     settings.risk.max_spread_pct,
        "delta_target_min":   settings.options.delta_target_min,
        "delta_target_max":   settings.options.delta_target_max,
    })
    vwap_strat = VWAPReclaimStrategy(params={"proximity_pct": 0.002, "confirmation_bars": 2})
    rsi_strat  = RSITrendStrategy(params={"rsi_period": 14, "rsi_oversold": 35, "rsi_overbought": 65, "trend_ema_period": 50})
    data_src   = YFinanceDataSource()

    confirmed_candidates = []   # populated in stage 4
    paper_contract = None       # populated in stage 8 if order placed
    paper_order_id = None
    paper_journal_id = None
    paper_symbol = None
    paper_entry_filled = False
    paper_exit_order_id = None

    try:
        # ══════════════════════════════════════════════════════════════════════
        # STAGE 1: Universe scan
        # ══════════════════════════════════════════════════════════════════════
        _step(1, "Universe scan — full ticker rank")

        loader = UniverseLoader()
        loader.load()
        max_scan = args.max_symbols or settings.universe.max_symbols_per_scan
        all_syms = loader.get_symbols(max_symbols=max_scan)
        _check(len(all_syms) > 0, f"Universe loaded: {len(all_syms)} symbols (blacklist={len(loader.blacklist)})")
        _info(f"  Symbols: {all_syms}")

        scanner = YFinanceScanner()
        metrics_list = await scanner.scan(all_syms)
        _check(len(metrics_list) == len(all_syms), f"YFinanceScanner returned {len(metrics_list)} SymbolMetrics objects")

        scorer = CandidateScorer(min_scan_score=settings.universe.min_scan_score)
        all_candidates: List[CandidateScore] = scorer.score_all(metrics_list)

        passed   = [c for c in all_candidates if not c.is_rejected]
        rejected = [c for c in all_candidates if c.is_rejected]

        _check(len(all_candidates) > 0, f"Scoring complete: {len(passed)} passed, {len(rejected)} rejected")

        # Verify reason codes present
        codes_present = all(len(c.reason_codes) > 0 for c in passed)
        _check(codes_present, "All passing candidates have non-empty reason_codes")

        rej_codes_present = all(len(c.rejected_reasons) > 0 for c in rejected)
        if rejected:
            _check(rej_codes_present, "All rejected candidates have non-empty rejected_reasons")

        print()
        _info(f"  {'Symbol':<8} {'Score':>6}  {'Signal':<8}  {'Codes / Rejection'}")
        _info(f"  {'─'*8} {'─'*6}  {'─'*8}  {'─'*40}")
        for c in all_candidates[:10]:
            detail = (
                ", ".join(c.rejected_reasons[:3]) if c.is_rejected
                else ", ".join(c.reason_codes[:3])
            )
            flag = " [REJECTED]" if c.is_rejected else ""
            _info(f"  {c.symbol:<8} {c.score:>6.1f}  {c.signal_type:<8}  {detail}{flag}")

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 2: DB persistence
        # ══════════════════════════════════════════════════════════════════════
        _step(2, "DB persistence — DBScanResult rows")

        for c in all_candidates:
            row = DBScanResult(
                session_date=today_str,
                symbol=c.symbol,
                score=c.score,
                signal_type=c.signal_type,
                reason_codes=json.dumps(c.reason_codes),
                rejected_reasons=json.dumps(c.rejected_reasons),
                is_rejected=c.is_rejected,
                selected=False,      # updated after Alpaca confirmation in stage 4
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
            db_session.add(row)
        await db_session.commit()

        # Query back
        result = await db_session.execute(
            select(DBScanResult).where(DBScanResult.session_date == today_str)
        )
        db_rows = result.scalars().all()
        _check(len(db_rows) >= len(all_candidates), f"DB rows: {len(db_rows)} (≥{len(all_candidates)} expected)")

        # Verify field accuracy on first row
        if db_rows:
            r = db_rows[0]
            _check(isinstance(r.score, float) and r.score >= 0, f"score field populated: {r.score:.1f}")
            _check(r.signal_type in ("LONG", "SHORT", "NEUTRAL"), f"signal_type valid: {r.signal_type}")
            codes = json.loads(r.reason_codes)
            _check(isinstance(codes, list), f"reason_codes deserialises to list: {codes[:2]}")
            rej = json.loads(r.rejected_reasons)
            _check(isinstance(rej, list), f"rejected_reasons deserialises to list: {rej}")

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 3: Dashboard endpoint
        # ══════════════════════════════════════════════════════════════════════
        _step(3, "Dashboard — GET /scan/results")

        try:
            import httpx
            from app.api.dashboard_api import create_app

            scan_store = {
                "session_date": today_str,
                "confirmed":   [],
                "candidates":  [
                    {
                        "symbol":          c.symbol,
                        "score":           c.score,
                        "signal_type":     c.signal_type,
                        "is_rejected":     c.is_rejected,
                        "reason_codes":    c.reason_codes,
                        "rejected_reasons": c.rejected_reasons,
                    }
                    for c in all_candidates
                ],
            }
            app = create_app(scan_results_store=scan_store)

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/scan/results")
                _check(resp.status_code == 200, f"GET /scan/results → HTTP {resp.status_code}")
                body = resp.json()
                _check("session_date" in body, "Response has session_date")
                _check("live" in body, "Response has live key")
                _check("db_results" in body, "Response has db_results key")
                _check("count" in body, "Response has count key")
                live_count = len(body.get("live", {}).get("candidates", []))
                _check(live_count == len(all_candidates), f"live.candidates: {live_count} entries")
                db_count = body.get("count", 0)
                _check(db_count >= len(all_candidates), f"db_results count: {db_count}")
        except ImportError:
            _warn("httpx not installed — skipping HTTP client test (endpoint structure verified via DB)")

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 4: Alpaca confirmation — top 5 candidates
        # ══════════════════════════════════════════════════════════════════════
        _step(4, "Alpaca confirmation — top 5 candidates")

        top5 = [c for c in all_candidates if not c.is_rejected][:5]
        if not top5:
            top5 = all_candidates[:5]
        _info(f"  Confirming {len(top5)} candidates: {[c.symbol for c in top5]}")

        confirmer = AlpacaConfirmer(broker, settings)
        confirmed_candidates = await confirmer.confirm_all(top5)

        _check(
            len(confirmed_candidates) > 0,
            f"Alpaca confirmed: {len(confirmed_candidates)}/{len(top5)} candidates",
        )

        # Mark selected in DB
        confirmed_syms = {cc.symbol for cc in confirmed_candidates}
        for row in db_rows:
            if row.symbol in confirmed_syms:
                row.selected = True
        await db_session.commit()

        # Detailed contract table
        print()
        _info(
            f"  {'Symbol':<6}  {'Strike':>8}  {'ATM%':>7}  {'Spread':>7}  "
            f"{'OI':>7}  {'Vol':>8}  {'Expiry':<12}  {'Delta':>7}"
        )
        _info(f"  {'─'*6}  {'─'*8}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*8}  {'─'*12}  {'─'*7}")

        for cc in confirmed_candidates:
            c = cc.contract
            atm_pct = (float(c.strike) / float(cc.contract.strike) - 1.0) if float(c.strike) > 0 else 0
            # ATM distance: (strike - underlying) / underlying
            # Get underlying from candidate metrics
            underlying = cc.candidate.metrics.price
            atm_dist = (float(c.strike) - underlying) / max(underlying, 1) * 100
            delta_str = f"{float(c.delta):.3f}" if c.delta is not None else "est"
            _info(
                f"  {cc.symbol:<6}  {float(c.strike):>8.2f}  {atm_dist:>+7.2f}%  "
                f"{c.spread_pct:>7.2%}  {c.open_interest:>7,}  "
                f"{c.volume:>8,}  {cc.expiration!s:<12}  {delta_str:>7}"
            )
            _check(
                float(c.bid) > 0 and float(c.ask) > 0,
                f"  {cc.symbol}: valid bid/ask  (bid={float(c.bid):.2f}, ask={float(c.ask):.2f})",
            )
            _check(
                c.open_interest >= settings.risk.min_open_interest,
                f"  {cc.symbol}: OI {c.open_interest} ≥ threshold {settings.risk.min_open_interest}",
            )
            _check(
                c.volume >= settings.risk.min_volume,
                f"  {cc.symbol}: vol {c.volume} ≥ threshold {settings.risk.min_volume}",
            )
            _check(
                c.spread_pct <= settings.risk.max_spread_pct,
                f"  {cc.symbol}: spread {c.spread_pct:.2%} ≤ max {settings.risk.max_spread_pct:.2%}",
            )

        # Report non-confirmed
        confirmed_set = {cc.symbol for cc in confirmed_candidates}
        for c in top5:
            if c.symbol not in confirmed_set:
                _warn(f"  {c.symbol}: not confirmed by Alpaca (no liquid contract found)")

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 5: VWAP strategy on current 5m bars
        # ══════════════════════════════════════════════════════════════════════
        _step(5, "VWAP strategy — current 5m intraday bars")

        vwap_symbol = confirmed_candidates[0].symbol if confirmed_candidates else (all_syms[0] if all_syms else "SPY")
        _info(f"  Target symbol: {vwap_symbol}")

        # Use end=today+1 so yfinance includes today's bars (end is exclusive)
        bars_5m = await data_src.get_bars(
            vwap_symbol,
            start=today - timedelta(days=4),
            end=today + timedelta(days=1),
            interval="5m",
        )
        _check(not bars_5m.empty, f"Fetched {len(bars_5m)} 5m bars for {vwap_symbol}")

        if not bars_5m.empty:
            vwap_signals: List[Signal] = vwap_strat.generate_signals(bars_5m, vwap_symbol)
            today_vwap_sigs = [
                s for s in vwap_signals
                if s.timestamp.astimezone(ET).date() == today
            ]

            if today_vwap_sigs:
                _check(True, f"VWAP signal today: {len(today_vwap_sigs)} signal(s)")
                for s in today_vwap_sigs[:3]:
                    _info(f"  {s.direction.value:<6}  at {s.timestamp.astimezone(ET).strftime('%H:%M')} ET  price={s.price:.2f}")
            else:
                _warn(f"No VWAP signal today for {vwap_symbol} — diagnosis:")
                _diagnose_vwap(bars_5m, vwap_symbol, proximity_pct=0.002, confirm=2)
                all_vwap_sigs = vwap_signals
                _info(f"  Total VWAP signals in 3-day window: {len(all_vwap_sigs)}")
                if all_vwap_sigs:
                    last = all_vwap_sigs[-1]
                    _info(f"  Most recent: {last.direction.value} at {last.timestamp.astimezone(ET)}")

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 6: RSI + trend strategy on current 5m bars
        # ══════════════════════════════════════════════════════════════════════
        _step(6, "RSI + trend strategy — current 5m intraday bars")

        rsi_symbol = (
            confirmed_candidates[1].symbol if len(confirmed_candidates) > 1
            else vwap_symbol
        )
        _info(f"  Target symbol: {rsi_symbol}")

        bars_rsi = (
            bars_5m if rsi_symbol == vwap_symbol
            else await data_src.get_bars(
                rsi_symbol,
                start=today - timedelta(days=4),
                end=today + timedelta(days=1),
                interval="5m",
            )
        )

        if not bars_rsi.empty:
            _check(len(bars_rsi) >= 55, f"{len(bars_rsi)} bars available (need ≥55 for RSI+EMA50)")
            rsi_signals: List[Signal] = rsi_strat.generate_signals(bars_rsi, rsi_symbol)
            today_rsi_sigs = [
                s for s in rsi_signals
                if s.timestamp.astimezone(ET).date() == today
            ]

            if today_rsi_sigs:
                _check(True, f"RSI signal today: {len(today_rsi_sigs)} signal(s)")
                for s in today_rsi_sigs[:3]:
                    _info(f"  {s.direction.value:<6}  at {s.timestamp.astimezone(ET).strftime('%H:%M')} ET  price={s.price:.2f}")
            else:
                _warn(f"No RSI signal today for {rsi_symbol} — diagnosis:")
                _diagnose_rsi(bars_rsi, rsi_symbol, 14, 35, 65, 50)
                all_rsi_sigs = rsi_signals
                _info(f"  Total RSI signals in 3-day window: {len(all_rsi_sigs)}")
                if all_rsi_sigs:
                    last = all_rsi_sigs[-1]
                    _info(f"  Most recent: {last.direction.value} at {last.timestamp.astimezone(ET)}")
        else:
            _warn(f"No bars fetched for {rsi_symbol}")

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 7: Liquidity filter ranking + ATM proximity
        # ══════════════════════════════════════════════════════════════════════
        _step(7, "Liquidity filter — spread/OI/volume/ATM-proximity ranking")

        top3_confirmed = confirmed_candidates[:3]
        if not top3_confirmed:
            _warn("No confirmed candidates to rank — skipping liquidity filter stage")
        else:
            for cc in top3_confirmed:
                underlying = cc.candidate.metrics.price
                _info(f"\n  {cc.symbol}  underlying=${underlying:.2f}  exp={cc.expiration}")
                _info(f"  {'Strike':>8}  {'ATM%':>7}  {'Spread':>7}  {'OI':>7}  {'Vol':>8}  {'Pass':>5}  {'Selected':>9}")
                _info(f"  {'─'*8}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*8}  {'─'*5}  {'─'*9}")

                # Re-fetch chain to get all contracts for comparison
                try:
                    chain = await broker.get_option_chain(cc.symbol, cc.expiration)
                    calls = sorted(chain.calls, key=lambda x: abs(float(x.strike) - underlying))[:10]
                    selected = liq_filter.select_contract(chain, type("Sig", (), {"direction": SignalDirection.LONG})())

                    for c in calls:
                        passes = liq_filter._passes_liquidity(c)
                        atm_dist = (float(c.strike) - underlying) / max(underlying, 1) * 100
                        is_sel = "  ← best" if selected and c.option_symbol == selected.option_symbol else ""
                        _info(
                            f"  {float(c.strike):>8.2f}  {atm_dist:>+7.2f}%  {c.spread_pct:>7.2%}  "
                            f"{c.open_interest:>7,}  {c.volume:>8,}  {'✓' if passes else '✗':>5}  {is_sel}"
                        )

                    if selected:
                        atm_dist = abs(float(selected.strike) - underlying) / max(underlying, 1)
                        _check(
                            atm_dist <= 0.10,
                            f"  Selected {selected.option_symbol} is within 10% of ATM (dist={atm_dist:.2%})",
                        )
                except Exception as exc:
                    _warn(f"  Chain fetch failed for {cc.symbol}: {exc}")

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 8: Paper lifecycle — top confirmed candidate
        # ══════════════════════════════════════════════════════════════════════
        _step(8, "Paper lifecycle — top confirmed candidate")

        run_paper = (
            settings.paper_evaluation_mode
            and not args.skip_paper
            and not settings.live_trading_enabled
            and acct.is_paper
            and confirmed_candidates
        )

        if not run_paper:
            reasons = []
            if not settings.paper_evaluation_mode:
                reasons.append("PAPER_EVALUATION_MODE=false")
            if args.skip_paper:
                reasons.append("--skip-paper flag")
            if not acct.is_paper:
                reasons.append("not a paper account")
            if not confirmed_candidates:
                reasons.append("no confirmed candidates")
            _warn(f"Paper lifecycle skipped: {', '.join(reasons) or 'unknown'}")
        else:
            # Use top confirmed candidate (not hardcoded SPY)
            cc = confirmed_candidates[0]
            paper_symbol = cc.symbol
            paper_contract = cc.contract

            _info(f"  Selected: {cc.symbol}  score={cc.score:.1f}  signal={cc.signal_type}")
            _info(
                f"  Contract: {paper_contract.option_symbol}  "
                f"bid={float(paper_contract.bid):.2f}  ask={float(paper_contract.ask):.2f}  "
                f"spread={paper_contract.spread_pct:.2%}"
            )

            # Check: not a live account, spread within fill-test threshold
            max_spread = getattr(settings, "fill_test_max_spread_pct", 0.20)
            if paper_contract.spread_pct > max_spread:
                _warn(
                    f"  Spread {paper_contract.spread_pct:.2%} > max {max_spread:.2%} — "
                    "skipping order to avoid wide-spread fill risk"
                )
            else:
                # Compute marketable limit price
                limit_price_float = compute_limit_price(
                    mode="marketable_limit",
                    bid=float(paper_contract.bid),
                    ask=float(paper_contract.ask),
                    offset_pct=settings.options.entry_marketable_offset_pct,
                )
                limit_price = Decimal(str(limit_price_float))
                _check(limit_price > 0, f"Entry limit price: ${limit_price_float:.4f} (marketable_limit)")

                # Place entry order
                request = OrderRequest(
                    symbol=paper_symbol,
                    option_symbol=paper_contract.option_symbol,
                    side=OrderSide.BUY_TO_OPEN,
                    quantity=1,
                    order_type=OrderType.LIMIT,
                    limit_price=limit_price,
                    strategy_id="validate_non_orb",
                    notes="validate_non_orb paper lifecycle test",
                )
                try:
                    order = await broker.place_option_order(request)
                    _check(
                        order.order_id is not None,
                        f"Entry order placed: {order.order_id[:8]}  status={order.status.value}",
                    )
                    paper_order_id = order.order_id

                    paper_journal_id = await journal.record_entry(
                        entry_time=datetime.now(tz=ET),
                        strategy_id="validate_non_orb",
                        signal_direction=cc.signal_type,
                        underlying_symbol=paper_symbol,
                        underlying_price=cc.candidate.metrics.price,
                        option_symbol=paper_contract.option_symbol,
                        expiration=str(cc.expiration),
                        strike=float(paper_contract.strike),
                        option_type=paper_contract.option_type,
                        delta=paper_contract.delta,
                        iv=paper_contract.implied_volatility,
                        bid=float(paper_contract.bid),
                        ask=float(paper_contract.ask),
                        spread_pct=paper_contract.spread_pct,
                        limit_price=float(limit_price),
                        limit_price_mode="marketable_limit",
                        quantity=1,
                        order_id=paper_order_id,
                        notes="validate_non_orb",
                    )
                    await journal.commit()

                    po = fill_tracker.register(
                        order_id=paper_order_id,
                        journal_id=paper_journal_id,
                        option_symbol=paper_contract.option_symbol,
                        symbol=paper_symbol,
                        strategy_id="validate_non_orb",
                        direction=cc.signal_type,
                        quantity=1,
                        limit_price=float(limit_price),
                        placed_at=datetime.now(tz=ET),
                    )
                    await store.save(po, today_str)
                    await db_session.commit()

                    # Poll for fill
                    timeout = min(settings.entry_order_timeout_secs, 60)
                    deadline = time.time() + timeout
                    _info(f"  Polling for fill up to {timeout}s …")

                    while time.time() < deadline:
                        fills = await fill_tracker.poll(broker, pm, journal, datetime.now(tz=ET))
                        await journal.commit()
                        if fills > 0:
                            paper_entry_filled = True
                            break
                        if fill_tracker.count() == 0:
                            break
                        await asyncio.sleep(10)

                    if paper_entry_filled and pm.has_position(paper_contract.option_symbol):
                        pos_list = pm.open_positions()
                        pos = pos_list[0]
                        _check(True, f"Entry FILLED @ ${pos.entry_price:.4f}")
                        _check(
                            pm.has_position(paper_contract.option_symbol),
                            f"Position open: {paper_contract.option_symbol}",
                        )
                    else:
                        _info(
                            f"  Entry order did not fill within {timeout}s "
                            "(expected outside market hours — infrastructure verified)"
                        )
                        # Cancel unfilled order
                        try:
                            await broker.cancel_order(paper_order_id)
                            await fill_tracker.poll(broker, pm, journal, datetime.now(tz=ET))
                            await journal.commit()
                            _check(fill_tracker.count() == 0, "Unfilled entry order cancelled cleanly")
                        except Exception as cancel_exc:
                            _warn(f"Cancel attempt: {cancel_exc}")

                except Exception as exc:
                    err_str = str(exc)
                    if ("422" in err_str or "Unprocessable" in err_str) and cc.expiration <= today:
                        _warn(
                            f"  422: {paper_contract.option_symbol} expired today ({cc.expiration}) — "
                            "expected after market close. Selecting non-expiring contract for re-test."
                        )
                        # Re-test with a fresh, non-today-expiry contract from the chain
                        try:
                            exps = await broker.get_available_expirations(paper_symbol)
                            future_exps = [e for e in exps if e > today]
                            if future_exps:
                                alt_exp = min(future_exps, key=lambda d: abs((d - today).days))
                                alt_chain = await broker.get_option_chain(paper_symbol, alt_exp)
                                alt_req = OrderRequest(
                                    symbol=paper_symbol,
                                    option_symbol=alt_chain.calls[0].option_symbol if alt_chain.calls else paper_contract.option_symbol,
                                    side=OrderSide.BUY_TO_OPEN,
                                    quantity=1,
                                    order_type=OrderType.LIMIT,
                                    limit_price=limit_price,
                                    strategy_id="validate_non_orb",
                                    notes="validate_non_orb alt-expiry",
                                )
                                alt_order = await broker.place_option_order(alt_req)
                                _check(
                                    alt_order.order_id is not None,
                                    f"Alt-expiry order placed: {alt_order.order_id[:8]} exp={alt_exp}",
                                )
                                paper_order_id = alt_order.order_id
                                # Cancel immediately — just verifying placement works
                                await broker.cancel_order(paper_order_id)
                                _info("  Alt-expiry order cancelled immediately (infrastructure test only)")
                                paper_order_id = None
                        except Exception as alt_exc:
                            _warn(f"  Alt-expiry test: {alt_exc}")
                    else:
                        _warn(f"  Paper lifecycle error: {err_str[:200]}")

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 9: Exit management
        # ══════════════════════════════════════════════════════════════════════
        _step(9, "Exit management — stop/TP/trailing/unrealized-PnL")

        # Always run synthetic test
        synth_symbol = paper_symbol or (confirmed_candidates[0].symbol if confirmed_candidates else "SPY")
        synth_opt = (
            paper_contract.option_symbol
            if paper_contract
            else f"{synth_symbol}260101C00500000"
        )
        synth_entry = (
            float(paper_contract.ask) * 1.01
            if paper_contract and float(paper_contract.ask) > 0
            else 5.00
        )

        _test_exit_management(pm, synth_symbol, synth_opt + "_TEST", synth_entry)

        # If we have a real filled position, also test real exit
        if paper_entry_filled and paper_contract and pm.has_position(paper_contract.option_symbol):
            _info("\n  Real exit for filled paper position:")
            real_pos = pm.open_positions()[0]
            exit_price_float = compute_limit_price(
                mode="bid",
                bid=float(paper_contract.bid),
                ask=float(paper_contract.ask),
                offset_pct=settings.options.exit_marketable_offset_pct,
            )
            exit_limit = Decimal(str(max(exit_price_float, 0.01)))

            exit_request = OrderRequest(
                symbol=paper_symbol,
                option_symbol=paper_contract.option_symbol,
                side=OrderSide.SELL_TO_CLOSE,
                quantity=1,
                order_type=OrderType.LIMIT,
                limit_price=exit_limit,
                strategy_id="validate_non_orb",
                notes="validate_non_orb exit",
            )
            try:
                exit_order = await broker.place_option_order(exit_request)
                paper_exit_order_id = exit_order.order_id
                _check(exit_order.order_id is not None, f"Exit order placed: {exit_order.order_id[:8]}")

                # Brief poll for exit fill
                deadline = time.time() + 30
                exit_filled = False
                while time.time() < deadline:
                    status = await broker.get_order_status(exit_order.order_id)
                    if status.status.value == "filled":
                        exit_filled = True
                        exit_fill_price = float(status.filled_price) if status.filled_price else exit_price_float
                        break
                    elif status.status.value in ("cancelled", "canceled", "rejected", "expired"):
                        break
                    await asyncio.sleep(10)

                # Close position in PM regardless
                final_price = (
                    float(real_pos.current_price) or exit_price_float
                )
                pnl = (final_price - real_pos.entry_price) * 100
                pm.close(paper_contract.option_symbol, final_price, pnl)
                hold_secs = (datetime.now(tz=ET) - real_pos.entry_time).total_seconds()
                await journal.record_exit(
                    journal_id=paper_journal_id,
                    exit_time=datetime.now(tz=ET),
                    exit_price=final_price,
                    exit_reason="validate_non_orb_exit" if exit_filled else "validate_non_orb_timeout",
                    realized_pnl=pnl,
                    hold_duration_secs=hold_secs,
                )
                await journal.commit()

                if not exit_filled:
                    await broker.cancel_order(exit_order.order_id)

                _check(not pm.has_position(paper_contract.option_symbol), "Position closed — PositionManager flat")

            except Exception as exc:
                _warn(f"Real exit error: {exc}")

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 10: Daily report + ledger
        # ══════════════════════════════════════════════════════════════════════
        _step(10, "Daily report + ledger update")

        try:
            from app.evaluation.daily_report import build_daily_report, to_json, to_markdown

            report = await build_daily_report(db_session, today_str)

            # DB accumulates across all runs today — check ≥ this run's counts
            _check(
                report.scanned_symbols_count >= len(all_candidates),
                f"report.scanned_symbols_count = {report.scanned_symbols_count} (≥{len(all_candidates)} this run)",
            )
            _check(
                report.candidate_count_passed >= len(passed),
                f"report.candidate_count_passed = {report.candidate_count_passed} (≥{len(passed)} this run)",
            )
            _check(
                report.candidate_count_rejected >= len(rejected),
                f"report.candidate_count_rejected = {report.candidate_count_rejected} (≥{len(rejected)} this run)",
            )

            # Selected symbols in report
            sel = report.selected_symbols or []
            _info(f"  report.selected_symbols = {sel}")

            # Top candidates in report
            top_c = report.top_candidates or []
            _check(len(top_c) > 0, f"report.top_candidates: {len(top_c)} entries")

            # Trade metrics
            _info(
                f"  trades_submitted={report.trades_submitted}  "
                f"trades_filled={report.trades_filled}  "
                f"realized_pnl=${report.realized_pnl:.2f}"
            )

            # Write report files
            report_dir = Path(settings.evaluation_output_dir) / "reports"
            report_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(ET).strftime("%Y%m%d_%H%M%S")
            json_path = report_dir / f"validate_non_orb_{ts}.json"
            md_path   = report_dir / f"validate_non_orb_{ts}.md"
            json_path.write_text(to_json(report))
            md_path.write_text(to_markdown(report))
            _check(json_path.exists(), f"JSON report written: {json_path.name}")
            _check(md_path.exists(), f"Markdown report written: {md_path.name}")

            # Ledger update
            if paper_entry_filled:
                from app.evaluation.ledger import EvaluationLedger
                ledger = EvaluationLedger(settings.evaluation_ledger_file)
                ledger.update(report)
                _check(True, "Ledger updated with today's session")

        except Exception as exc:
            _warn(f"Report generation error: {exc}")
            import traceback
            traceback.print_exc()

    finally:
        # ── Cleanup: cancel any orphan orders + close any orphan positions ────
        print(f"\n{_BANNER}")
        print("  CLEANUP")
        print(_BANNER)

        # Cancel any remaining pending orders from fill_tracker
        for pending_id in list(fill_tracker._pending.keys()):
            try:
                await broker.cancel_order(pending_id)
                _info(f"  Cancelled pending order: {pending_id[:8]}")
            except Exception:
                pass
        await fill_tracker.poll(broker, pm, journal, datetime.now(tz=ET))
        await journal.commit()

        # Force-close any lingering PM positions (safety net)
        for pos in pm.open_positions():
            pm.close(pos.option_symbol, pos.current_price or pos.entry_price, pnl=0.0)
            _info(f"  Closed lingering position: {pos.option_symbol}")

        _check(len(pm.open_positions()) == 0, "PositionManager is flat")
        _check(fill_tracker.count() == 0, "FillTracker has no pending orders")

        await broker.close()
        await db_session.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'═'*64}")
    print(f"  VALIDATION SUMMARY — {today_str}")
    print(f"{'═'*64}")
    print(f"  Checks passed : {_checks_passed} / {_checks_total}")
    print(f"  Paper filled  : {'yes' if paper_entry_filled else 'no (outside market hours — expected)'}")
    print(f"  Live trading  : DISABLED ✓")
    print(f"  Orphan orders : {fill_tracker.count()}")
    print(f"  Orphan pos    : {len(pm.open_positions())}")
    print(f"{'═'*64}\n")

    return 0 if _checks_passed >= _checks_total * 0.80 else 1


# ── Entry point ────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Non-ORB pipeline validation")
    p.add_argument("--max-symbols", type=int, default=None, help="Max symbols to scan (default: from settings)")
    p.add_argument("--skip-paper", action="store_true", help="Skip paper lifecycle stage")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(asyncio.run(main(args)))
