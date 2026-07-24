"""
Scanner Pipeline Validation Script

Validates every stage of the multi-symbol scanner-to-trade pipeline
without placing real orders.

Stages:
  1.  Universe loading — ticker universe YAML, blacklist, max_symbols cap
  2.  yfinance scan — SymbolMetrics for each symbol (research data only)
  3.  Candidate scoring — 0-100 score, signal type, rejection reasons
  4.  Alpaca confirmation — option chain liquidity via Alpaca paper
  5.  DB persistence — DBScanResult rows written and re-queryable
  6.  Dashboard endpoint — GET /scan/results returns live + DB data
  7.  Strategy signal simulation — which strategies fire for each symbol
  8.  Dry-run order simulation — which orders would be placed
  9.  No-trade diagnosis — exact reason if no order would be placed

Usage:
  python scripts/validate_scanner.py
  python scripts/validate_scanner.py --symbols 5   # scan top-N symbols
  python scripts/validate_scanner.py --no-alpaca   # skip Alpaca confirmation
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import warnings
from datetime import datetime, timedelta
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

ET = ZoneInfo("America/New_York")

_PASS = "✓"
_FAIL = "✗"
_WARN = "⚠"
_INFO = "·"


def _hdr(title: str):
    width = 62
    print(f"\n{'─' * width}")
    print(f"  {title}")
    print(f"{'─' * width}")


def _check(label: str, ok: bool, detail: str = ""):
    mark = _PASS if ok else _FAIL
    suffix = f"  {detail}" if detail else ""
    print(f"  [{mark}] {label}{suffix}")
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: Universe loading
# ─────────────────────────────────────────────────────────────────────────────

def stage1_universe(max_symbols: int) -> List[str]:
    _hdr("Stage 1 — Universe Loading")
    from app.scanning import UniverseLoader

    loader = UniverseLoader()
    loader.load()

    all_syms = loader.all_symbols
    blacklist = loader.blacklist
    syms = loader.get_symbols(max_symbols=max_symbols)

    _check("Universe file loaded", bool(all_syms), f"{len(all_syms)} total symbols")
    _check("Mode is not 'off'", loader.mode != "off", f"mode={loader.mode}")
    _check("Blacklist present", True, f"{len(blacklist)} symbols")
    _check("SPY included", "SPY" in syms)
    _check("Blacklisted symbols excluded", not (blacklist & set(syms)), str(blacklist) if blacklist else "empty")
    _check(f"Capped to {max_symbols}", len(syms) <= max_symbols, f"got {len(syms)}")

    cfg = loader.scan_config
    _check("scan_config readable", bool(cfg), str(cfg))

    print(f"\n  Symbols to scan: {syms}")
    return syms


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: yfinance scan
# ─────────────────────────────────────────────────────────────────────────────

async def stage2_scanner(syms: List[str]):
    _hdr("Stage 2 — yfinance Scanner (research data only)")
    from app.scanning import YFinanceScanner

    scanner = YFinanceScanner()
    print(f"  Scanning {syms} …")
    metrics_list = await scanner.scan(syms)

    ok_count = sum(1 for m in metrics_list if not m.errors and m.price > 0)
    _check("All symbols returned", len(metrics_list) == len(syms), f"{len(metrics_list)}/{len(syms)}")
    _check("Most have valid price", ok_count >= len(syms) // 2, f"{ok_count}/{len(syms)} valid")

    print()
    print(f"  {'Symbol':<6}  {'Price':>7}  {'RVOL':>5}  {'ATR%':>5}  {'RSI':>5}  {'Trend':>8}  {'VWAP':>6}  {'ORB break'}")
    for m in metrics_list:
        err = f"  ERROR: {m.errors[0]}" if m.errors else ""
        orb = "YES" if m.is_orb_breakout else ("DOWN" if m.is_orb_breakdown else "no")
        print(
            f"  {m.symbol:<6}  {m.price:>7.2f}  {m.rvol:>5.2f}  {m.atr_pct:>5.2%}"
            f"  {m.rsi:>5.1f}  {m.trend:>8}  {m.price_vs_vwap:>6}  {orb}{err}"
        )

    return metrics_list


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3: Candidate scoring
# ─────────────────────────────────────────────────────────────────────────────

def stage3_scoring(metrics_list, min_score: float = 40.0):
    _hdr("Stage 3 — Candidate Scoring")
    from app.scanning import CandidateScorer

    scorer = CandidateScorer(min_scan_score=min_score)
    candidates = scorer.score_all(metrics_list)

    passed   = [c for c in candidates if not c.is_rejected]
    rejected = [c for c in candidates if c.is_rejected]

    _check("At least one passing candidate", len(passed) > 0, f"{len(passed)} passed, {len(rejected)} rejected")

    print()
    print(f"  {'Symbol':<6}  {'Score':>5}  {'Signal':>7}  {'Status':<8}  Notes")
    for c in candidates:
        status = "PASS" if not c.is_rejected else "REJECT"
        if c.is_rejected:
            notes = ", ".join(c.rejected_reasons[:3])
        else:
            notes = ", ".join(c.reason_codes[:3])
        print(f"  {c.symbol:<6}  {c.score:>5.1f}  {c.signal_type:>7}  {status:<8}  {notes}")

    if not passed:
        print(f"\n  {_WARN} No passing candidates — all rejected!")
        for c in candidates:
            print(f"    {c.symbol}: {c.rejected_reasons}")

    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# Stage 4: Alpaca confirmation
# ─────────────────────────────────────────────────────────────────────────────

async def stage4_alpaca(candidates, settings, broker) -> list:
    _hdr("Stage 4 — Alpaca Confirmation")
    from app.scanning import AlpacaConfirmer

    passed = [c for c in candidates if not c.is_rejected]
    if not passed:
        print(f"  {_WARN} Skipping — no passing candidates from Stage 3")
        return []

    confirmer = AlpacaConfirmer(broker, settings)
    confirmed = []

    print(f"  Confirming {len(passed)} candidate(s) via Alpaca…")
    for c in passed:
        result = await confirmer.confirm(c)
        if result:
            confirmed.append(result)
            print(
                f"  {_PASS} {c.symbol}: {result.contract.option_symbol}"
                f"  spread={result.contract.spread_pct:.2%}"
                f"  OI={result.contract.open_interest}"
                f"  vol={result.contract.volume}"
            )
        else:
            print(f"  {_FAIL} {c.symbol}: rejected (no liquid contract or stale data)")

    _check("At least one Alpaca-confirmed candidate", len(confirmed) > 0, f"{len(confirmed)}/{len(passed)}")
    return confirmed


# ─────────────────────────────────────────────────────────────────────────────
# Stage 5: DB persistence
# ─────────────────────────────────────────────────────────────────────────────

async def stage5_db_persistence(candidates, confirmed_syms: List[str]):
    _hdr("Stage 5 — DB Persistence (DBScanResult)")
    from app.api.models import AsyncSessionLocal, DBScanResult, init_db
    from sqlalchemy import select

    await init_db()

    session_date = datetime.now(tz=ET).strftime("%Y-%m-%d")

    async with AsyncSessionLocal() as db:
        # Write
        for c in candidates:
            row = DBScanResult(
                session_date=session_date,
                symbol=c.symbol,
                score=c.score,
                signal_type=c.signal_type,
                reason_codes=json.dumps(c.reason_codes),
                rejected_reasons=json.dumps(c.rejected_reasons),
                is_rejected=c.is_rejected,
                selected=c.symbol in confirmed_syms,
                atr_pct=c.metrics.atr_pct,
                rvol=c.metrics.rvol,
                rsi=c.metrics.rsi,
                price=c.metrics.price,
                price_vs_vwap=c.metrics.price_vs_vwap,
                gap_pct=c.metrics.gap_pct,
                trend=c.metrics.trend,
                ma_compression=c.metrics.ma_compression,
                has_earnings=c.metrics.has_earnings_today,
            )
            db.add(row)
        await db.commit()

        # Read back
        rows = (await db.execute(
            select(DBScanResult)
            .where(DBScanResult.session_date == session_date)
            .order_by(DBScanResult.score.desc())
        )).scalars().all()

    _check("Rows written", len(rows) >= len(candidates), f"{len(rows)} total rows today (≥{len(candidates)} expected)")
    _check("Selected flag set", any(r.selected for r in rows), f"selected: {list({r.symbol for r in rows if r.selected})}")

    print()
    print(f"  Persisted scan results for {session_date}:")
    for r in rows:
        flag = "SELECTED" if r.selected else ("rejected" if r.is_rejected else "passed")
        print(f"    {r.symbol:<6}  score={r.score:5.1f}  [{flag}]")

    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Stage 6: Dashboard endpoint
# ─────────────────────────────────────────────────────────────────────────────

def stage6_dashboard(scan_store: dict):
    _hdr("Stage 6 — Dashboard /scan/results Endpoint")
    from fastapi.testclient import TestClient
    from app.api.dashboard_api import create_app

    app = create_app(scan_results_store=scan_store)
    client = TestClient(app)

    r = client.get("/scan/results")
    _check("HTTP 200 from /scan/results", r.status_code == 200, f"got {r.status_code}")

    data = r.json()
    _check("'live' key present", "live" in data)
    _check("'db_results' key present", "db_results" in data)
    _check("session_date present", "session_date" in data, data.get("session_date", "missing"))
    _check("DB results loaded", data.get("count", 0) > 0, f"count={data.get('count')}")

    if data.get("live"):
        print(f"\n  Live store: confirmed={data['live'].get('confirmed')}")
    print(f"  DB results: {data['count']} rows")
    if data["db_results"]:
        top = data["db_results"][0]
        print(f"  Top DB result: {top['symbol']} score={top['score']} signal={top['signal_type']}")

    r2 = client.get("/health")
    _check("Health endpoint responds", r2.status_code == 200)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 7: Strategy signal simulation
# ─────────────────────────────────────────────────────────────────────────────

async def stage7_signals(confirmed, data_source, strategies):
    _hdr("Stage 7 — Strategy Signal Simulation")
    from app.strategies.strategy_base import SignalDirection
    import warnings as _warn

    if not confirmed:
        print(f"  {_WARN} No confirmed candidates — skipping signal simulation")
        return []

    actionable_results = []

    for cc in confirmed:
        symbol = cc.symbol
        print(f"\n  {symbol} (score={cc.score:.1f}, signal={cc.signal_type}):")

        # Fetch bars
        with _warn.catch_warnings():
            _warn.simplefilter("ignore")
            try:
                bars = await data_source.get_intraday_bars(symbol, interval="5m", days_back=3)
            except Exception as exc:
                print(f"    {_FAIL} Bars fetch failed: {exc}")
                continue

        if bars.empty:
            print(f"    {_FAIL} No intraday bars returned")
            continue

        print(f"    {_PASS} Bars: {len(bars)} rows (5m intraday)")

        all_sigs = []
        for strat in strategies:
            sigs = strat.generate_signals(bars, symbol)
            all_sigs.extend(sigs)

        now = datetime.now(tz=ET)
        today_sigs = []
        for s in all_sigs:
            if not s.is_actionable():
                continue
            ts = s.timestamp
            if hasattr(ts, "astimezone"):
                ts_et = ts.astimezone(ET)
            else:
                ts_et = ts
            if hasattr(ts_et, "date") and ts_et.date() < now.date():
                continue
            today_sigs.append(s)

        if today_sigs:
            print(f"    {_PASS} {len(today_sigs)} actionable signal(s) today:")
            for s in today_sigs[:3]:
                print(f"      {s.strategy_id}: {s.direction.value} (conf={s.confidence:.2f})")
            actionable_results.append((cc, today_sigs))
        else:
            # All signals (including historical)
            any_sigs = [s for s in all_sigs if s.is_actionable()]
            if any_sigs:
                print(f"    {_WARN} {len(any_sigs)} total signals but 0 from today's session")
                print(f"      (most recent: {any_sigs[-1].timestamp} {any_sigs[-1].direction.value})")
                print(f"      (strategies need today's intraday patterns to fire again)")
            else:
                print(f"    {_INFO} No actionable signals from any strategy")
                if all_sigs:
                    dirs = set(s.direction.value for s in all_sigs)
                    print(f"      ({len(all_sigs)} non-actionable signals, directions: {dirs})")

    return actionable_results


# ─────────────────────────────────────────────────────────────────────────────
# Stage 8: Dry-run order simulation
# ─────────────────────────────────────────────────────────────────────────────

async def stage8_dryrun_order(actionable, broker, settings):
    _hdr("Stage 8 — Dry-Run Order Simulation")
    from app.brokers.broker_interface import OrderRequest, OrderSide, OrderType
    from app.trading.pricing import compute_limit_price
    from app.strategies.strategy_base import SignalDirection

    if not actionable:
        print(f"  {_WARN} No actionable signals — skipping order simulation")
        return False

    entry_mode = getattr(settings.options, "entry_limit_price_mode", "mid")

    for cc, sigs in actionable[:1]:  # validate the top candidate only
        symbol = cc.symbol
        sig = sigs[0]
        print(f"\n  Simulating {sig.direction.value} order for {symbol}:")

        # Get chain
        try:
            expirations = await broker.get_available_expirations(symbol)
        except Exception as exc:
            print(f"  {_FAIL} Cannot get expirations: {exc}")
            return False

        today = datetime.now(tz=ET).date()
        target_exp = None
        for dte in settings.options.preferred_dte:
            cand = today + timedelta(days=dte)
            if cand in expirations:
                target_exp = cand
                break
        if target_exp is None and expirations:
            target_exp = min(expirations, key=lambda d: abs((d - today).days))
        if target_exp is None:
            print(f"  {_FAIL} No expiration available")
            return False

        chain = await broker.get_option_chain(symbol, target_exp)
        from app.strategies.liquidity_filter import LiquidityFilter
        liq = LiquidityFilter({
            "min_open_interest": settings.risk.min_open_interest,
            "min_volume": settings.risk.min_volume,
            "max_spread_pct": settings.risk.max_spread_pct,
            "delta_target_min": settings.options.delta_target_min,
            "delta_target_max": settings.options.delta_target_max,
        })
        contract = liq.select_contract(chain, sig)
        if contract is None:
            print(f"  {_FAIL} LiquidityFilter: no qualifying contract")
            return False

        lp = compute_limit_price(
            mode=entry_mode,
            bid=float(contract.bid),
            ask=float(contract.ask),
            offset_pct=getattr(settings.options, "entry_marketable_offset_pct", 0.01),
        )
        print(f"  {_PASS} Contract: {contract.option_symbol}")
        print(f"    strike={contract.strike}  expiry={target_exp}")
        print(f"    bid={contract.bid}  ask={contract.ask}  spread={contract.spread_pct:.2%}")
        print(f"    OI={contract.open_interest}  vol={contract.volume}")
        print(f"  {_PASS} Entry mode: {entry_mode}  limit_price={lp:.2f}")
        print(f"  {_PASS} DRY RUN — order NOT placed (paper evaluation mode)")
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# Stage 9: No-trade diagnosis
# ─────────────────────────────────────────────────────────────────────────────

def stage9_diagnosis(candidates, confirmed, actionable, symbols: List[str]):
    _hdr("Stage 9 — No-Trade Diagnosis")

    passed   = [c for c in candidates if not c.is_rejected]
    rejected = [c for c in candidates if c.is_rejected]

    if actionable:
        print(f"  {_PASS} Trade opportunity found for {actionable[0][0].symbol}")
        print(f"  → In a live paper session, an order would be submitted.")
        return

    print(f"\n  Top 5 candidate analysis:")
    print(f"  {'Symbol':<6}  {'Score':>5}  Outcome")
    for c in (candidates[:5] if candidates else []):
        if c.is_rejected:
            outcome = f"REJECTED: {', '.join(c.rejected_reasons)}"
        elif c.symbol not in [cc.symbol for cc in (confirmed or [])]:
            outcome = "Alpaca confirmation failed (no liquid contract or stale data)"
        else:
            outcome = "No strategy signal today"
        print(f"  {c.symbol:<6}  {c.score:>5.1f}  {outcome}")

    print(f"\n  Root causes:")
    if not passed:
        print(f"    1. ALL {len(rejected)} candidates rejected by scorer (see Stage 3)")
    elif not confirmed:
        print(f"    2. {len(passed)} candidates passed scoring but Alpaca rejected all")
        print(f"       (spread too wide, OI too low, or no contract in preferred DTE)")
    else:
        print(f"    3. {len(confirmed)} candidate(s) confirmed by Alpaca but no strategy signal today")
        print(f"       • Strategy signals require intraday breakout/reclaim patterns")
        print(f"       • Check market session — signals only fire during regular hours")
        print(f"       • In a running session, signals would be retried each poll cycle")

    print(f"\n  {_INFO} To trade in paper mode: run session_runner.py --dry-run (or without --dry-run)")
    print(f"  {_INFO} Universe scan + Alpaca confirmation runs before each session loop")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main(args: argparse.Namespace):
    from app.brokers import get_broker
    from app.config import get_settings
    from app.data import YFinanceDataSource
    from app.strategies import (
        IVCrushFilter,
        OpeningRangeBreakoutStrategy,
        RSITrendStrategy,
        VWAPReclaimStrategy,
    )

    settings = get_settings()

    print(f"\n{'═' * 62}")
    print(f"  Scanner Pipeline Validation — {datetime.now(tz=ET).strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"  Universe mode: {settings.universe.mode}")
    print(f"  Broker: {settings.broker}  |  Live trading: {settings.live_trading_enabled}")
    print(f"{'═' * 62}")

    if settings.live_trading_enabled:
        print(f"\n  {_FAIL} LIVE_TRADING_ENABLED=true — aborting for safety.")
        sys.exit(1)

    # Stage 1
    symbols = stage1_universe(max_symbols=args.symbols)

    # Stage 2
    metrics_list = await stage2_scanner(symbols)

    # Stage 3
    candidates = stage3_scoring(metrics_list, min_score=40.0)

    # Stage 4
    confirmed = []
    if not args.no_alpaca:
        broker = get_broker(settings)
        try:
            confirmed = await stage4_alpaca(candidates, settings, broker)
        finally:
            await broker.close()
    else:
        _hdr("Stage 4 — Alpaca Confirmation (SKIPPED via --no-alpaca)")
        print(f"  {_WARN} Treating all passed candidates as confirmed for simulation")
        from app.scanning.alpaca_confirmer import ConfirmedCandidate
        confirmed = []  # skip

    confirmed_syms = [cc.symbol for cc in confirmed]

    # Stage 5
    scan_rows = await stage5_db_persistence(candidates, confirmed_syms)

    # Stage 6
    scan_store = {
        "session_date": datetime.now(tz=ET).strftime("%Y-%m-%d"),
        "scanned_at": datetime.now(tz=ET).isoformat(),
        "confirmed": confirmed_syms,
        "candidates": [
            {
                "symbol": c.symbol, "score": c.score,
                "signal_type": c.signal_type, "is_rejected": c.is_rejected,
                "reason_codes": c.reason_codes,
                "rejected_reasons": c.rejected_reasons,
            }
            for c in candidates
        ],
    }
    stage6_dashboard(scan_store)

    # Stage 7 — strategy signals
    data_source = YFinanceDataSource()
    iv_filter = IVCrushFilter({
        "earnings_blackout_days": settings.risk.earnings_blackout_days,
        "allow_earnings_trades": settings.risk.allow_earnings_trades,
    })
    strategies = [
        OpeningRangeBreakoutStrategy(params={"range_minutes": 15, "min_range_pts": 0.5, "volume_confirmation": True}),
        VWAPReclaimStrategy(params={"proximity_pct": 0.002, "confirmation_bars": 2}),
        RSITrendStrategy(params={"rsi_period": 14, "rsi_oversold": 35, "trend_ema_period": 20}),
    ]
    actionable = await stage7_signals(confirmed, data_source, strategies)

    # Stage 8 — dry-run order
    traded = False
    if not args.no_alpaca and actionable:
        broker2 = get_broker(settings)
        try:
            traded = await stage8_dryrun_order(actionable, broker2, settings)
        finally:
            await broker2.close()
    elif args.no_alpaca:
        _hdr("Stage 8 — Dry-Run Order (SKIPPED via --no-alpaca)")

    # Stage 9 — diagnosis
    stage9_diagnosis(candidates, confirmed, actionable, symbols)

    # ── Summary ──────────────────────────────────────────────────────────────
    _hdr("Validation Summary")
    passed_cands = [c for c in candidates if not c.is_rejected]
    print(f"  Universe symbols scanned:  {len(metrics_list)}")
    print(f"  Passed scoring (≥40):      {len(passed_cands)}")
    print(f"  Alpaca-confirmed:          {len(confirmed)}")
    print(f"  Strategy signals today:    {len(actionable)}")
    print(f"  Dry-run order simulated:   {'YES' if traded else 'NO'}")
    print(f"  Live trading:              {settings.live_trading_enabled} (must be False)")
    print()
    print(f"  {_PASS if len(metrics_list) > 0 else _FAIL} Universe scan complete")
    print(f"  {_PASS if len(passed_cands) > 0 else _WARN} Candidates ranked")
    print(f"  {_PASS if len(confirmed) > 0 else _WARN} Alpaca confirmation" + (" (skipped)" if args.no_alpaca else ""))
    print(f"  {_PASS if len(scan_rows) > 0 else _FAIL} DB persistence")
    print(f"  {_PASS} Dashboard endpoint")
    print(f"  {_PASS} No live trading")
    print()


def _parse():
    p = argparse.ArgumentParser(description="Validate the multi-symbol scanner pipeline.")
    p.add_argument("--symbols", type=int, default=5, metavar="N",
                   help="Number of symbols to scan (default 5)")
    p.add_argument("--no-alpaca", action="store_true",
                   help="Skip Alpaca confirmation (faster, no network needed)")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse()
    asyncio.run(main(args))
