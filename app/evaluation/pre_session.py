"""
Pre-session checklist for paper evaluation mode.

All checks are async. Checks are classified as:
  required  — session will NOT start if any of these fail
  advisory  — logged as warnings but do not block session start

Usage:
    checks = await run_pre_session_checks(settings, broker, db_session, risk)
    if not all_required_pass(checks):
        print(format_check_table(checks))
        sys.exit(2)
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

from app.trading.quote_evidence import parse_quote_timestamp, quote_is_fresh, valid_quote

logger = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")
_DATA_CHECK_TIMEOUT_SECONDS = 5.0
_BAR_INTERVAL = timedelta(minutes=5)
_MAX_BAR_CLOSE_AGE_SECONDS = 600


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    required: bool = True
    status: Optional[str] = None


def all_required_pass(checks: List[CheckResult]) -> bool:
    return all(c.passed for c in checks if c.required)


def format_check_table(checks: List[CheckResult]) -> str:
    lines = ["Pre-session checklist:"]
    for c in checks:
        if c.passed:
            icon = "✓"
        elif c.required:
            icon = "✗"
        else:
            icon = "⚠"
        req_label = "required" if c.required else "advisory"
        status = f"{c.status.upper()}: " if c.status else ""
        lines.append(f"  {icon}  [{req_label:8}]  {c.name:30s}  {status}{c.message}")
    ok = all_required_pass(checks)
    lines.append("")
    lines.append("  Result: PASS" if ok else "  Result: FAIL — session aborted")
    return "\n".join(lines)


async def run_pre_session_checks(
    settings,
    broker=None,
    db_session=None,
    risk_manager=None,
) -> List[CheckResult]:
    """Run all pre-session checks and return results."""
    checks: List[CheckResult] = []

    checks.append(_check_paper_mode(settings))
    checks.append(_check_kill_switch_inactive(settings))
    checks.append(await _check_market_day(broker))
    checks.append(await _check_broker_reachable(broker))
    checks.append(await _check_db_writable(db_session))
    checks.append(_check_logs_writable(settings))
    checks.append(_check_daily_loss_reset(risk_manager))
    checks.append(await _check_no_stale_pending_orders(db_session))
    checks.append(await _check_data_feed_freshness(broker, settings))

    for c in checks:
        level = logging.INFO if c.passed or c.status == "warming_up" else (logging.ERROR if c.required else logging.WARNING)
        status = c.status.upper() if c.status else ("PASS" if c.passed else "FAIL")
        logger.log(level, "pre_session [%s] %s: %s", status, c.name, c.message)

    return checks


# ── Individual checks ─────────────────────────────────────────────────────────

def _check_paper_mode(settings) -> CheckResult:
    if getattr(settings, "live_trading_enabled", False):
        return CheckResult(
            name="paper_mode_confirmed",
            passed=False,
            message="LIVE_TRADING_ENABLED=true — evaluation mode requires paper trading only",
        )
    return CheckResult(
        name="paper_mode_confirmed",
        passed=True,
        message="Live trading disabled — paper mode confirmed",
    )


def _check_kill_switch_inactive(settings) -> CheckResult:
    active = settings.is_kill_switch_active()
    return CheckResult(
        name="kill_switch_inactive",
        passed=not active,
        message=(
            "Kill switch is ACTIVE — remove the kill switch file before starting"
            if active
            else "Kill switch inactive"
        ),
    )


async def _check_market_day(broker=None) -> CheckResult:
    now = datetime.now(tz=_ET)
    day_label = now.strftime("%A %Y-%m-%d")
    # Fast path: weekends are never trading days
    if now.weekday() >= 5:
        return CheckResult(
            name="market_day",
            passed=False,
            message=f"Today is {now.strftime('%A')} — market closed (weekend)",
            required=False,
        )
    # Weekday: query the broker calendar to catch US market holidays
    if broker is not None and hasattr(broker, "is_market_session_today"):
        try:
            is_session = await broker.is_market_session_today()
            return CheckResult(
                name="market_day",
                passed=is_session,
                message=(
                    f"Today is {day_label} — trading day"
                    if is_session
                    else f"Today is {day_label} — market closed (holiday)"
                ),
                required=False,
            )
        except Exception:
            pass  # fall through to weekday-only check on API error
    # Fallback: weekday heuristic (no holiday awareness)
    return CheckResult(
        name="market_day",
        passed=True,
        message=f"Today is {day_label} — trading day (calendar check skipped)",
        required=False,
    )


async def _check_broker_reachable(broker) -> CheckResult:
    if broker is None:
        return CheckResult(
            name="broker_reachable",
            passed=False,
            message="No broker provided",
        )
    try:
        acct = await broker.get_account()
        is_paper = getattr(acct, "is_paper", True)
        if not is_paper:
            return CheckResult(
                name="broker_reachable",
                passed=False,
                message="Broker account is a LIVE account — evaluation mode requires paper account",
            )
        return CheckResult(
            name="broker_reachable",
            passed=True,
            message=f"Broker reachable — paper={is_paper}",
        )
    except Exception as exc:
        return CheckResult(
            name="broker_reachable",
            passed=False,
            message=f"Broker unreachable: {exc}",
        )


async def _check_db_writable(db_session) -> CheckResult:
    if db_session is None:
        return CheckResult(
            name="db_writable",
            passed=False,
            message="No database session provided",
        )
    try:
        from sqlalchemy import text
        await db_session.execute(text("SELECT 1"))
        return CheckResult(name="db_writable", passed=True, message="Database accessible and writable")
    except Exception as exc:
        return CheckResult(name="db_writable", passed=False, message=f"Database error: {exc}")


def _check_logs_writable(settings) -> CheckResult:
    try:
        log_dir = Path(getattr(settings, "log_file", "./logs/trading.log")).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        probe = log_dir / ".pre_session_probe"
        probe.write_text("ok")
        probe.unlink()
        return CheckResult(
            name="logs_writable",
            passed=True,
            message=f"Log directory writable: {log_dir}",
        )
    except Exception as exc:
        return CheckResult(
            name="logs_writable",
            passed=False,
            message=f"Log directory not writable: {exc}",
        )


def _check_daily_loss_reset(risk_manager) -> CheckResult:
    if risk_manager is None:
        return CheckResult(
            name="daily_loss_reset",
            passed=True,
            message="No risk manager (dry-run or not yet initialized)",
            required=False,
        )
    try:
        pnl = float(getattr(risk_manager, "daily_pnl", 0))
        if pnl != 0.0:
            return CheckResult(
                name="daily_loss_reset",
                passed=False,
                message=f"Daily PnL = {pnl:.2f} (expected 0 at session start — possible double-start?)",
            )
        return CheckResult(name="daily_loss_reset", passed=True, message="Daily PnL at zero — fresh session")
    except Exception as exc:
        return CheckResult(
            name="daily_loss_reset",
            passed=True,
            message=f"Could not check daily PnL: {exc}",
            required=False,
        )


async def _check_no_stale_pending_orders(db_session) -> CheckResult:
    if db_session is None:
        return CheckResult(
            name="no_stale_pending_orders",
            passed=True,
            message="No DB session (dry-run)",
            required=False,
        )
    try:
        from sqlalchemy import select
        from app.api.models import DBPendingOrder

        today = str(date.today())
        rows = (
            await db_session.execute(
                select(DBPendingOrder)
                .where(DBPendingOrder.status == "pending")
                .where(DBPendingOrder.session_date != today)
            )
        ).scalars().all()

        if rows:
            sample = [r.order_id[:8] for r in rows[:3]]
            return CheckResult(
                name="no_stale_pending_orders",
                passed=False,
                message=(
                    f"{len(rows)} stale pending order(s) from prior session(s): "
                    f"{sample}… — cancel them or run with --cancel-pending"
                ),
            )
        return CheckResult(
            name="no_stale_pending_orders",
            passed=True,
            message="No stale pending orders from prior sessions",
        )
    except Exception as exc:
        return CheckResult(
            name="no_stale_pending_orders",
            passed=True,
            message=f"Stale order check skipped: {exc}",
            required=False,
        )


async def _check_data_feed_freshness(broker=None, settings=None, *, now=None) -> CheckResult:
    """Advisory SPY equity quote and completed-bar check using source timestamps.

    The first regular-session five-minute bar cannot complete before 09:35.
    Warm-up is explicitly unverified. Existing scanner and option-entry gates
    continue to own entry eligibility; this check does not change their rules.
    """
    def result(status, message):
        return CheckResult("data_feed_fresh", status == "fresh", message,
                           required=False, status=status)

    requested_at = now if now is not None else datetime.now(tz=_ET)
    if parse_quote_timestamp(requested_at) is None:
        return result("unavailable", "Readiness check requires an aware observation time")
    requested_at = requested_at.astimezone(_ET)
    try:
        open_h, open_m = map(int, getattr(settings, "market_open", "09:30").split(":"))
        close_h, close_m = map(int, getattr(settings, "market_close", "16:00").split(":"))
        opened = requested_at.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
        closed = requested_at.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    except (AttributeError, TypeError, ValueError):
        return result("unavailable", "Market session hours are unavailable; data readiness is unverified")
    if requested_at.weekday() >= 5 or requested_at >= closed:
        return result("market_closed", "Regular-session data readiness is not asserted outside market hours")
    if broker is None or not callable(getattr(broker, "get_quote", None)):
        return result("unavailable", "No broker equity quote source; data freshness is unverified")
    warmup = requested_at < opened + _BAR_INTERVAL

    async def fetch():
        quote = await broker.get_quote("SPY")
        if warmup:
            return quote, []
        bars = await broker.get_stock_bars("SPY", start=opened, end=requested_at, timeframe="5Min")
        return quote, bars

    try:
        quote, bars = await asyncio.wait_for(fetch(), timeout=_DATA_CHECK_TIMEOUT_SECONDS)
        observed_at = requested_at if now is not None else datetime.now(tz=_ET)
        timestamp = parse_quote_timestamp(getattr(quote, "timestamp", None))
        quote_ok = (valid_quote(float(quote.bid), float(quote.ask))
                    and quote_is_fresh(timestamp, observed_at))
        age = (observed_at - timestamp).total_seconds() if timestamp else None
        quote_text = (f"SPY equity quote age={age:.1f}s" if age is not None
                      else "SPY equity quote timestamp unavailable")
        if warmup:
            return result("warming_up", f"First completed regular-session 5Min bar is expected at "
                          f"{(opened + _BAR_INTERVAL):%H:%M} ET; {quote_text}; "
                          f"quote_valid_and_fresh={quote_ok}; bar readiness unverified")
        completed = []
        for value, price in bars:
            ts = parse_quote_timestamp(value)
            if ts is None or not math.isfinite(float(price)) or float(price) <= 0:
                continue
            if ts > observed_at:
                return result("unverified", f"{quote_text}; future-dated SPY bar received")
            if opened <= ts and ts + _BAR_INTERVAL <= observed_at:
                completed.append(ts + _BAR_INTERVAL)
        if not completed:
            return result("unverified", f"{quote_text}; no valid completed regular-session SPY 5Min bars")
        bar_age = (observed_at - max(completed)).total_seconds()
        ready = quote_ok and 0 <= bar_age <= _MAX_BAR_CLOSE_AGE_SECONDS
        return result("fresh" if ready else "unverified",
                      f"{quote_text}; quote_valid_and_fresh={quote_ok}; "
                      f"latest completed SPY 5Min bar close age={bar_age:.1f}s "
                      f"(quote limit=60s, bar-close limit={_MAX_BAR_CLOSE_AGE_SECONDS}s)")
    except Exception as exc:
        return result("unavailable", f"SPY market-data readiness unverified: {type(exc).__name__}")
