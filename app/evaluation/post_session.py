"""
Post-session workflow for paper evaluation mode.

Steps (in order):
  1. Cancel stale pending orders from today's session
  2. Reconcile positions (verify broker vs local state)
  3. Warn if any unexpected open positions remain
  4. Build daily evaluation report (JSON + Markdown)
  5. Send summary alert
  6. Save session artifacts to evaluation_output_dir/reports/
  7. Update cumulative ledger

Returns a PostSessionResult dataclass summarising each step outcome.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PostSessionResult:
    session_date: str
    stale_orders_cancelled: int = 0
    cancel_errors: List[str] = field(default_factory=list)
    positions_reconciled: bool = False
    open_positions_remaining: int = 0
    report_path_json: Optional[str] = None
    report_path_md: Optional[str] = None
    ledger_updated: bool = False
    alert_sent: bool = False
    errors: List[str] = field(default_factory=list)


async def run_post_session(
    settings,
    broker=None,
    db_session=None,
    fill_tracker=None,
    pm=None,
    journal=None,
    alert_service=None,
    session_date: Optional[str] = None,
) -> PostSessionResult:
    """Run the full post-session workflow and return a summary."""
    today = session_date or str(date.today())
    result = PostSessionResult(session_date=today)

    # 1. Cancel stale pending orders
    await _cancel_stale_pending(broker, fill_tracker, db_session, today, result)

    # 2. Reconcile positions
    await _reconcile_positions(broker, pm, result)

    # 3. Warn on open positions
    _check_open_positions(pm, result)

    # 4 + 5 + 6. Build report, save artifacts, send alert
    report = await _build_and_save_report(db_session, settings, today, alert_service, result)

    # 7. Update ledger
    if report is not None:
        await _update_ledger(report, db_session, today, settings, result)

    # Log summary
    logger.info(
        "Post-session complete | date=%s | cancelled=%d | report=%s | ledger=%s",
        today,
        result.stale_orders_cancelled,
        result.report_path_json,
        result.ledger_updated,
    )
    return result


# ── Step implementations ──────────────────────────────────────────────────────


async def _cancel_stale_pending(broker, fill_tracker, db_session, today: str, result: PostSessionResult):
    if fill_tracker is None:
        return

    for pending in list(fill_tracker.pending_orders()):
        try:
            if broker:
                await broker.cancel_order(pending.order_id)
            result.stale_orders_cancelled += 1
            logger.info("Post-session: cancelled pending order %s", pending.order_id[:8])
        except Exception as exc:
            msg = f"Could not cancel {pending.order_id[:8]}: {exc}"
            result.cancel_errors.append(msg)
            logger.warning("Post-session: %s", msg)

    # Also mark any DB-persisted pending orders for today as stale
    if db_session is not None:
        try:
            from sqlalchemy import select, update
            from app.api.models import DBPendingOrder

            await db_session.execute(
                update(DBPendingOrder)
                .where(DBPendingOrder.session_date == today)
                .where(DBPendingOrder.status == "pending")
                .values(status="stale_eod")
            )
            await db_session.commit()
        except Exception as exc:
            msg = f"DB pending-order cleanup error: {exc}"
            result.errors.append(msg)
            logger.warning("Post-session: %s", msg)


async def _reconcile_positions(broker, pm, result: PostSessionResult):
    if broker is None or pm is None:
        return
    try:
        broker_positions = await broker.get_positions()
        local_positions = pm.open_positions()
        local_symbols = {p.option_symbol for p in local_positions}
        broker_symbols = {p.option_symbol for p in broker_positions}

        orphaned = broker_symbols - local_symbols
        phantom = local_symbols - broker_symbols

        if orphaned:
            logger.warning("Post-session: %d orphaned broker position(s) not in local state: %s",
                           len(orphaned), list(orphaned)[:5])
        if phantom:
            logger.warning("Post-session: %d phantom local position(s) not in broker: %s",
                           len(phantom), list(phantom)[:5])

        result.positions_reconciled = True
    except Exception as exc:
        msg = f"Reconciliation error: {exc}"
        result.errors.append(msg)
        logger.warning("Post-session: %s", msg)


def _check_open_positions(pm, result: PostSessionResult):
    if pm is None:
        return
    open_pos = pm.open_positions()
    result.open_positions_remaining = len(open_pos)
    if open_pos:
        symbols = [p.option_symbol for p in open_pos[:5]]
        logger.warning(
            "Post-session: %d open position(s) remain after EOD — "
            "this is unexpected: %s",
            len(open_pos), symbols,
        )


async def _build_and_save_report(db_session, settings, today: str, alert_service, result: PostSessionResult):
    if db_session is None:
        result.errors.append("No DB session — cannot build report")
        return None

    try:
        from app.evaluation.daily_report import (
            build_daily_report, send_summary_alert, to_json, to_markdown,
        )

        # Compute ORB forward performance before building the report so that
        # orb_fwd_* columns are populated when the report queries signal_bridge.
        if getattr(settings, "paper_eval_permissive_entry_mode", False):
            try:
                from app.evaluation.orb_forward_performance import compute_orb_forward_performance
                fwd_updated = await compute_orb_forward_performance(db_session, today)
                logger.info("Post-session: ORB forward performance: %d rows updated", fwd_updated)
            except Exception as exc:
                logger.warning("Post-session: ORB forward performance failed: %s", exc)

        report = await build_daily_report(db_session, today, settings)

        # Determine output dir
        output_dir = Path(getattr(settings, "evaluation_output_dir", "./evaluation")) / "reports"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save JSON
        json_path = output_dir / f"{today}.json"
        json_path.write_text(to_json(report))
        result.report_path_json = str(json_path)
        logger.info("Post-session: evaluation report saved → %s", json_path)

        # Save Markdown
        md_path = output_dir / f"{today}.md"
        md_path.write_text(to_markdown(report))
        result.report_path_md = str(md_path)

        # Send alert
        try:
            await send_summary_alert(report, alert_service)
            result.alert_sent = True
        except Exception as exc:
            logger.warning("Post-session: alert failed: %s", exc)

        return report

    except Exception as exc:
        msg = f"Report generation failed: {exc}"
        result.errors.append(msg)
        logger.error("Post-session: %s", msg, exc_info=True)
        return None


async def _update_ledger(report, db_session, today: str, settings, result: PostSessionResult):
    try:
        from app.evaluation.ledger import EvaluationLedger
        from sqlalchemy import select
        from app.api.models import DBTradeJournal

        trade_records = []
        if db_session is not None:
            try:
                trade_records = (
                    await db_session.execute(
                        select(DBTradeJournal).where(DBTradeJournal.session_date == today)
                    )
                ).scalars().all()
            except Exception as exc:
                logger.warning("Post-session: could not load trade records for ledger: %s", exc)

        ledger_file = getattr(settings, "evaluation_ledger_file", "./evaluation/ledger.json")
        ledger = EvaluationLedger.load(ledger_file)
        ledger.add_session(report, trade_records=list(trade_records))
        ledger.save()
        result.ledger_updated = True

    except Exception as exc:
        msg = f"Ledger update failed: {exc}"
        result.errors.append(msg)
        logger.error("Post-session: %s", msg, exc_info=True)
