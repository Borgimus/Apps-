#!/usr/bin/env python3
"""
Controlled Alpaca paper-order smoke test.

Exercises the full single-order lifecycle with real Alpaca paper credentials:
  1. Safety guards (paper mode, eval mode, non-live URL)
  2. Broker connectivity + is_paper=True
  3. Pre-session checklist — all required checks must pass
  4. SPY option chain + near-ATM call contract selected
  5. Risk check (pass or log correct rejection)
  6. Limit order placed at $0.01 — won't fill, guaranteed pending
  7. Journal entry recorded + order persisted to DB via PendingOrderStore
  8. FillTracker registers the order
  9. FillTracker poll — order still pending (0 fills expected)
 10. Order cancelled at broker
 11. Second FillTracker poll — detects cancellation, removes from pending
 12. run_post_session() — report JSON + ledger written
 13. Verify report fields + ledger cumulative stats

Aborts immediately if LIVE_TRADING_ENABLED=true or non-paper URL detected.
Exit 0 on full pass, 1 if any step fails.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import warnings
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set these before any app import so settings picks them up.
os.environ.setdefault("PAPER_EVALUATION_MODE", "true")
os.environ.setdefault("RISK_MAX_TRADES_PER_DAY", "1")
# Expand market hours so the session-buffer risk check always passes.
os.environ.setdefault("MARKET_OPEN", "00:00")
os.environ.setdefault("MARKET_CLOSE", "23:59")

ET = ZoneInfo("America/New_York")

_pass_count = 0
_fail_count = 0


def _step(label: str, passed: bool, detail: str = "") -> bool:
    global _pass_count, _fail_count
    tag = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{tag}] {label}{suffix}")
    if passed:
        _pass_count += 1
    else:
        _fail_count += 1
    return passed


async def main() -> int:
    print(f"\n{'='*62}")
    print("  Alpaca Paper-Order Smoke Test")
    print(f"{'='*62}\n")

    from app.api.models import AsyncSessionLocal, init_db
    from app.brokers import get_broker
    from app.brokers.broker_interface import OrderRequest, OrderSide, OrderType
    from app.config import get_settings
    from app.evaluation.post_session import run_post_session
    from app.evaluation.pre_session import (
        all_required_pass,
        format_check_table,
        run_pre_session_checks,
    )
    from app.risk import RiskManager
    from app.trading import FillTracker, PositionManager, TradeJournal
    from app.trading.pending_order_store import PendingOrderStore
    from app.utils.alerting import AlertConfig, AlertService

    settings = get_settings()
    now = datetime.now(tz=ET)
    today_str = now.strftime("%Y-%m-%d")

    # ── 1. Safety guards ──────────────────────────────────────────────────────
    print("── 1. Safety guards")
    _step("LIVE_TRADING_ENABLED is false", not settings.live_trading_enabled)
    _step(
        "ALPACA_BASE_URL is paper endpoint",
        "paper-api" in settings.alpaca_base_url.lower(),
        settings.alpaca_base_url,
    )
    _step("PAPER_EVALUATION_MODE is true", settings.paper_evaluation_mode)

    if settings.live_trading_enabled or "paper-api" not in settings.alpaca_base_url.lower():
        print("\n  ABORT: live trading path detected — refusing to continue.\n")
        return 1

    # ── 2. Broker connectivity ────────────────────────────────────────────────
    print("\n── 2. Broker connectivity")
    broker = get_broker(settings)
    try:
        acct = await broker.get_account()
        _step(
            "Account reachable, is_paper=True",
            acct.is_paper,
            f"equity=${float(acct.equity):,.2f}",
        )
    except Exception as exc:
        _step("Account reachable", False, str(exc))
        await broker.close()
        return 1

    # ── 3. DB + pre-session checklist ─────────────────────────────────────────
    print("\n── 3. Pre-session checklist")
    await init_db()
    db_session = AsyncSessionLocal()
    journal = TradeJournal(db_session, is_paper=True)
    store = PendingOrderStore(db_session)
    alert_service = AlertService(AlertConfig.from_env())
    risk = RiskManager(settings)
    risk.start_session(acct.equity)
    pm = PositionManager(settings)
    fill_tracker = FillTracker(max_age_minutes=30, store=store, alert_service=alert_service)

    checks = await run_pre_session_checks(
        settings=settings, broker=broker, db_session=db_session, risk_manager=risk,
    )
    print(format_check_table(checks))
    required_ok = all_required_pass(checks)
    _step("All required checks pass", required_ok)

    if not required_ok:
        await broker.close()
        await db_session.close()
        return 1

    # ── 4. Option chain + contract selection ──────────────────────────────────
    print("\n── 4. Option chain")
    expirations = await broker.get_available_expirations("SPY")
    # Prefer tomorrow's expiry to avoid same-day expiry edge cases.
    target_exp = next((e for e in expirations if e > now.date()), None)
    if target_exp is None and expirations:
        target_exp = expirations[-1]

    if target_exp is None:
        _step("Expiration available", False, "no expirations returned by broker")
        await broker.close()
        await db_session.close()
        return 1

    _step("Expiration selected", True, str(target_exp))

    chain = await broker.get_option_chain("SPY", target_exp)
    spy_price = float(chain.underlying_price) if chain.underlying_price else 0.0
    _step("SPY option chain fetched", bool(chain.calls or chain.puts),
          f"calls={len(chain.calls)} puts={len(chain.puts)} spy={spy_price:.2f}")

    # Near-ATM call: prefer contracts with live quotes; fall back to any call.
    quoted = [c for c in chain.calls if float(c.bid) > 0 or float(c.ask) > 0]
    candidates = quoted or chain.calls
    if not candidates:
        _step("Near-ATM call contract available", False, "chain has 0 calls")
        await broker.close()
        await db_session.close()
        return 1

    contract = min(candidates, key=lambda c: abs(float(c.strike) - spy_price))
    _step(
        "Near-ATM call selected",
        True,
        f"{contract.option_symbol} strike={float(contract.strike):.2f} "
        f"bid={float(contract.bid):.2f} ask={float(contract.ask):.2f}",
    )

    # ── 5. Risk check ─────────────────────────────────────────────────────────
    print("\n── 5. Risk check")
    limit_price = Decimal("0.01")  # deliberately non-fillable
    request = OrderRequest(
        symbol="SPY",
        option_symbol=contract.option_symbol,
        side=OrderSide.BUY_TO_OPEN,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=limit_price,
        strategy_id="paper_smoke_test",
        notes="paper_order_smoke_test",
    )
    risk_result = risk.check_order(
        request=request,
        equity=acct.equity,
        contract=contract,
        now=now,
    )
    if risk_result.passed:
        _step("Risk check approved", True, risk_result.summary())
        request.quantity = risk_result.approved_quantity
    else:
        # Correctly rejected — acceptance criteria allows this outcome.
        _step(
            "Order correctly rejected by risk/liquidity rules",
            True,
            "; ".join(risk_result.messages),
        )
        print("\n  Risk rejected the order — skipping placement. Running post-session.")
        post_result = await run_post_session(
            settings=settings, broker=broker, db_session=db_session,
            fill_tracker=fill_tracker, pm=pm, journal=journal,
            alert_service=alert_service, session_date=today_str,
        )
        _step("Post-session report generated",
              bool(post_result.report_path_json),
              post_result.report_path_json or str(post_result.errors))
        _step("Ledger updated", post_result.ledger_updated)
        await broker.close()
        await db_session.close()
        _print_summary()
        return 0 if _fail_count == 0 else 1

    # ── 6. Place limit order ──────────────────────────────────────────────────
    print("\n── 6. Order placement")
    try:
        order = await broker.place_option_order(request)
        _step(
            "Limit order submitted",
            bool(order.order_id),
            f"id={order.order_id[:8]} status={order.status.value}",
        )
    except Exception as exc:
        _step("Limit order submitted", False, str(exc))
        await broker.close()
        await db_session.close()
        return 1

    # ── 7. DB persistence + FillTracker registration ──────────────────────────
    print("\n── 7. DB persistence + FillTracker")
    try:
        journal_id = await journal.record_entry(
            entry_time=now,
            strategy_id="paper_smoke_test",
            signal_direction="LONG",
            underlying_symbol="SPY",
            underlying_price=spy_price,
            option_symbol=contract.option_symbol,
            expiration=str(target_exp),
            strike=float(contract.strike),
            option_type="call",
            delta=contract.delta,
            iv=contract.implied_volatility,
            bid=float(contract.bid),
            ask=float(contract.ask),
            spread_pct=contract.spread_pct,
            limit_price=float(limit_price),
            quantity=request.quantity,
            order_id=order.order_id,
            notes="paper_smoke_test",
        )
        await journal.commit()

        po = fill_tracker.register(
            order_id=order.order_id,
            journal_id=journal_id,
            option_symbol=contract.option_symbol,
            symbol="SPY",
            strategy_id="paper_smoke_test",
            direction="LONG",
            quantity=request.quantity,
            limit_price=float(limit_price),
            placed_at=now,
        )
        await store.save(po, today_str)
        await db_session.commit()

        _step(
            "Journal entry recorded + order persisted to DB",
            True,
            f"journal_id={journal_id}",
        )
        _step(
            "FillTracker registered order",
            fill_tracker.count() == 1,
            f"pending_count={fill_tracker.count()}",
        )
    except Exception as exc:
        _step("DB persistence + FillTracker registration", False, str(exc))
        await broker.close()
        await db_session.close()
        return 1

    # ── 8. FillTracker poll — should be pending, 0 fills ─────────────────────
    print("\n── 8. FillTracker poll (expect 0 fills)")
    fills = await fill_tracker.poll(broker, pm, journal, now, risk)
    _step(
        "Poll returns 0 fills (order not filled)",
        fills == 0,
        f"fills={fills} still_pending={fill_tracker.count()}",
    )

    # ── 9. Cancel order at broker ─────────────────────────────────────────────
    print("\n── 9. Cancel order")
    cancelled = await broker.cancel_order(order.order_id)
    _step("Broker accepted cancel", cancelled, f"cancel_result={cancelled}")

    # Brief pause for paper engine state propagation, then re-poll.
    await asyncio.sleep(1)
    fills2 = await fill_tracker.poll(broker, pm, journal, now, risk)
    _step(
        "FillTracker detects cancellation, removes from pending",
        fill_tracker.count() == 0,
        f"remaining_pending={fill_tracker.count()}",
    )

    # ── 10. Post-session ──────────────────────────────────────────────────────
    print("\n── 10. Post-session evaluation")
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
    _step(
        "Post-session completed without errors",
        not post_result.errors,
        f"errors={post_result.errors or 'none'}",
    )

    # ── 11. Verify artifacts ──────────────────────────────────────────────────
    print("\n── 11. Artifact verification")
    report_path = post_result.report_path_json
    report_ok = bool(report_path) and Path(report_path).exists()
    _step("Evaluation report JSON written", report_ok, report_path or "not written")
    _step("Markdown report written",
          bool(post_result.report_path_md) and Path(post_result.report_path_md).exists(),
          post_result.report_path_md or "not written")
    _step("Ledger updated", post_result.ledger_updated)

    if report_ok:
        data = json.loads(Path(report_path).read_text())
        _step(
            "Report date matches today",
            data.get("date") == today_str,
            f"date={data.get('date')}",
        )
        # submitted=1 because we recorded the journal entry; cancelled=1 after poll
        _step(
            "Report records the submitted order",
            data.get("trades_submitted", 0) >= 1,
            f"trades_submitted={data.get('trades_submitted')} "
            f"trades_cancelled={data.get('trades_cancelled')}",
        )

    ledger_path = Path(settings.evaluation_ledger_file)
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text())
        trading_days = ledger.get("cumulative", {}).get("trading_days", 0)
        _step(
            "Ledger cumulative stats present",
            trading_days >= 1,
            f"trading_days={trading_days}",
        )
    else:
        _step("Ledger file exists", False, str(ledger_path))

    await broker.close()
    await db_session.close()

    _print_summary()
    return 0 if _fail_count == 0 else 1


def _print_summary():
    total = _pass_count + _fail_count
    print(f"\n{'='*62}")
    print(f"  Results: {_pass_count}/{total} passed, {_fail_count} failed")
    print(f"{'='*62}\n")


if __name__ == "__main__":
    from app.utils.logging_setup import configure_logging
    configure_logging(level="WARNING")
    sys.exit(asyncio.run(main()))
