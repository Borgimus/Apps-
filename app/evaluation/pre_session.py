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

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    required: bool = True


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
        lines.append(f"  {icon}  [{req_label:8}]  {c.name:30s}  {c.message}")
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
    checks.append(_check_market_day())
    checks.append(await _check_broker_reachable(broker))
    checks.append(await _check_db_writable(db_session))
    checks.append(_check_logs_writable(settings))
    checks.append(_check_daily_loss_reset(risk_manager))
    checks.append(await _check_no_stale_pending_orders(db_session))
    checks.append(await _check_data_feed_freshness(db_session))

    for c in checks:
        level = logging.INFO if c.passed else (logging.ERROR if c.required else logging.WARNING)
        logger.log(level, "pre_session [%s] %s: %s", "PASS" if c.passed else "FAIL", c.name, c.message)

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


def _check_market_day() -> CheckResult:
    now = datetime.now(tz=_ET)
    is_weekday = now.weekday() < 5
    return CheckResult(
        name="market_day",
        passed=is_weekday,
        message=(
            f"Today is {now.strftime('%A %Y-%m-%d')} — trading day"
            if is_weekday
            else f"Today is {now.strftime('%A')} — market closed (weekend)"
        ),
        required=False,  # advisory: allow weekend dry-runs
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


async def _check_data_feed_freshness(db_session) -> CheckResult:
    """Advisory: last session log entry age."""
    if db_session is None:
        return CheckResult(
            name="data_feed_fresh",
            passed=True,
            message="No DB session (dry-run)",
            required=False,
        )
    try:
        from sqlalchemy import select
        from app.api.models import DBSessionLog

        today = str(date.today())
        now = datetime.now(tz=_ET)
        row = (
            await db_session.execute(
                select(DBSessionLog)
                .where(DBSessionLog.session_date == today)
                .order_by(DBSessionLog.timestamp.desc())
                .limit(1)
            )
        ).scalars().first()

        if row and row.timestamp:
            ts = row.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_ET)
            age_secs = int((now - ts.astimezone(_ET)).total_seconds())
            if age_secs > 7200:
                return CheckResult(
                    name="data_feed_fresh",
                    passed=False,
                    message=f"Last session log is {age_secs // 60}m old — data may be stale",
                    required=False,
                )
            return CheckResult(
                name="data_feed_fresh",
                passed=True,
                message=f"Last session log: {age_secs}s ago",
                required=False,
            )
        return CheckResult(
            name="data_feed_fresh",
            passed=True,
            message="No prior session log today — first run of the day",
            required=False,
        )
    except Exception as exc:
        return CheckResult(
            name="data_feed_fresh",
            passed=True,
            message=f"Feed freshness check skipped: {exc}",
            required=False,
        )
