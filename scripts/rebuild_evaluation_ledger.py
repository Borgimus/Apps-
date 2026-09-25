#!/usr/bin/env python3
"""Rebuild trade metrics from a read-only SQLite backup into a NEW ledger.

This reconciles the journal internally. Broker fills remain a separate check.
Existing cohort/contamination annotations are preserved. No orders or DB writes.
"""

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.evaluation.daily_report import DailyReport, StrategyStats
from app.evaluation.ledger import EvaluationLedger


def rebuild(database: Path, source: Path, output: Path) -> dict:
    database, source, output = database.resolve(), source.resolve(), output.resolve()
    if not database.is_file():
        raise ValueError("SQLite backup does not exist")
    if output.exists() or output in (database, source):
        raise ValueError("Output must be a new path, separate from the source ledger and database")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute("SELECT * FROM trade_journal ORDER BY session_date, exit_time, id")]

    ledger = EvaluationLedger.load(str(source))
    if ledger._load_errors:
        raise ValueError("Source ledger is malformed; preserve and repair it before rebuilding")
    ledger.ledger_file = output
    old = {s.date: s for s in ledger.sessions}
    dates = sorted({row["session_date"] for row in rows if row.get("session_date")})
    errors = {}
    order_counts = Counter(r.get("order_id") for r in rows if r.get("status") == "closed" and r.get("order_id"))
    for day in dates:
        records = []
        day_errors = []
        for row in (r for r in rows if r.get("session_date") == day):
            record = dict(row)
            for field in ("entry_time", "exit_time", "filled_at"):
                value = record.get(field)
                if value:
                    record[field] = datetime.fromisoformat(value)
            records.append(SimpleNamespace(**record))
            if row["status"] != "closed":
                continue
            oid = row.get("order_id")
            if not oid or not row.get("exit_order_id"):
                day_errors.append(f"Trade {row['id']}: missing broker order identifier")
            if oid and order_counts[oid] > 1:
                day_errors.append(f"Trade {row['id']}: duplicate entry order identifier")
            try:
                qty = row.get("filled_quantity") or row.get("quantity")
                entry, exit_price, pnl = (float(row[k]) for k in ("fill_price", "exit_price", "realized_pnl"))
                if not all(math.isfinite(v) for v in (entry, exit_price, pnl, qty)) or qty <= 0 or int(qty) != qty or entry <= 0 or exit_price < 0:
                    raise ValueError("Invalid fill values")
                if not math.isclose((exit_price - entry) * 100 * qty, pnl, rel_tol=0, abs_tol=0.011):
                    day_errors.append(f"Trade {row['id']}: fill arithmetic mismatch")
            except (ValueError, TypeError, KeyError):
                day_errors.append(f"Trade {row['id']}: incomplete or invalid fill values")

        known = [t for t in records if t.status == "closed" and t.realized_pnl is not None and math.isfinite(t.realized_pnl)]
        strategies = []
        for sid in sorted({t.strategy_id for t in known}):
            pnls = [t.realized_pnl for t in known if t.strategy_id == sid]
            strategies.append(StrategyStats(strategy_id=sid, wins=sum(p > 0 for p in pnls),
                                           losses=sum(p < 0 for p in pnls), realized_pnl=sum(pnls)))
        previous = old.get(day)
        from app.trading.health_report import HealthReporter
        report = DailyReport(
            date=day, session_start=None, session_end=None,
            realized_pnl=sum(t.realized_pnl for t in known), by_strategy=strategies,
            unrealized_pnl=sum(getattr(t, "unrealized_pnl", 0) or 0 for t in records if t.status == "open"),
            trades_submitted=sum(t.status != "rejected" for t in records),
            trades_filled=sum(getattr(t, "fill_price", None) is not None and t.status in ("closed", "open", "cancelled") for t in records),
            trades_cancelled=sum(t.status == "cancelled" for t in records),
            trades_rejected=sum(t.status == "rejected" for t in records),
            max_drawdown=HealthReporter._max_drawdown([t.realized_pnl for t in known]),
        )
        entry = ledger.add_session(report, records)
        if previous:
            for attr in ("phase", "contamination_flags", "data_clean", "notes", "api_errors", "kill_switch_events",
                         "slippage_total", "spread_cost_total"):
                setattr(entry, attr, getattr(previous, attr))
            if not math.isclose(previous.realized_pnl, entry.realized_pnl, rel_tol=0, abs_tol=0.011):
                day_errors.append("Prior ledger P&L differs from journal; broker reconciliation required")
        entry.trade_metric_errors.extend(day_errors)
        entry.trade_metrics_complete = entry.trade_metrics_complete and not day_errors
        if entry.trade_metric_errors:
            errors[day] = entry.trade_metric_errors

    # Dates missing from a partial backup retain their historical rows and lose verification.
    for entry in ledger.sessions:
        if entry.date not in dates:
            entry.trade_metrics_complete = False
            entry.trade_metric_errors = ["Session absent from supplied database backup"]
            errors[entry.date] = entry.trade_metric_errors
    manifest = {
        "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
        "journal_rows": len(rows), "rebuilt_dates": dates,
        "broker_fills_reconciled": False, "session_errors": errors,
        "rows_missing_session_date": sum(not r.get("session_date") for r in rows),
        "trade_records": rows,
    }
    if manifest["rows_missing_session_date"]:
        ledger._load_errors.append("Journal rows missing session date require reconciliation")
    # Stage the complete deliverable and publish it only after JSON validation.
    staged = output.with_name(output.name + ".staging")
    if staged.exists():
        raise ValueError("Staging path already exists; choose a new output path")
    ledger.ledger_file = staged
    try:
        ledger.save()
        payload = json.loads(staged.read_text())
        payload["rebuild_manifest"] = manifest
        staged.write_text(json.dumps(payload, indent=2, allow_nan=False))
        # Exclusive creation also prevents a concurrently created output being overwritten.
        with output.open("x") as destination:
            destination.write(staged.read_text())
    finally:
        staged.unlink(missing_ok=True)
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database", type=Path, required=True, help="SQLite backup, never the running database")
    ap.add_argument("--ledger", type=Path, default=Path("evaluation/ledger.json"))
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    result = rebuild(args.database, args.ledger, args.output)
    print(f"Created {args.output}: {result['journal_rows']} journal rows; "
          f"{len(result['session_errors'])} sessions need repair. Broker fills still require reconciliation.")


if __name__ == "__main__":
    main()
