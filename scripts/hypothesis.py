#!/usr/bin/env python3
"""Local research operations, independent of the broker and scheduled observer."""
from __future__ import annotations

import argparse
import fcntl
from datetime import date
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from dotenv import dotenv_values

from app.hypotheses.registry import REGISTRY, load_registry, register, sha, transition, verify_registration
from app.research.history.bars import AlpacaHistory, cache_root
from app.research.history.daily import daily_closes
from app.research.replay.calibrate import frozen_parity, random_entry_rejection
from app.research.replay.report import json_write, save_report
from app.research.replay.sources import ArchiveSource, HistoricalSource


def rule_for(root, h):
    from app.hypotheses.sector import sector_map
    if h == "S1":
        from app.hypotheses.s1_sector_reversal import SectorReversal
        return SectorReversal(sector_map(root))
    if h == "S2":
        from app.hypotheses.s2_failed_breakdown import FailedBreakdown
        return FailedBreakdown()
    if h == "S3":
        from app.hypotheses.s3_relative_strength import RelativeStrength
        return RelativeStrength(sector_map(root))
    raise ValueError("Only intraday S1–S3 use stock-bar replay")


def history(cache):
    path = Path(os.getenv("TRADER_RESEARCH_ENV", str(ROOT / ".env")))
    config = dotenv_values(path, interpolate=False)
    return AlpacaHistory(os.getenv("ALPACA_API_KEY") or config.get("ALPACA_API_KEY"),
                         os.getenv("ALPACA_SECRET_KEY") or config.get("ALPACA_SECRET_KEY"), cache)


def intraday_source(history, rule, feed):
    symbols = list(dict.fromkeys(("SPY", *rule.symbols)))
    panels = history.morning_panels(symbols, date(2016, 1, 1), date(2026, 11, 21), feed=feed)
    return HistoricalSource(panels)


def progress(message):
    print(message, file=sys.stderr, flush=True)


def historical_screen(root, h, feed, cache):
    if h in {"S1", "S2", "S3"}:
        from app.research.replay.study import study

        rule = rule_for(root, h)
        with history(cache) as source:
            data = study(root, intraday_source(source, rule, feed), rule, scratch_dir=cache,
                         progress=progress)
        return {**data, "feed": feed, "availability_model": "bar close + 60 seconds",
                "coverage_note": "IEX volume reflects one venue" if feed == "iex" else "SIP historical bars"}
    if h == "R1":
        from app.hypotheses.r1_sector_rotation import SECTORS, screen
        data = daily_closes(["SPY", *SECTORS], date(1999, 1, 1), date(2026, 11, 21), cache)
    elif h == "V1":
        from app.hypotheses.v1_implied_vs_realized import screen
        data = daily_closes(["SPY", "^VIX", "^VIX9D"], date(2000, 1, 1), date(2026, 11, 21), cache)
    else:
        raise ValueError("No screen registered for this ID")
    return {**screen(data), "id": h, "feed": "yfinance daily",
            "input_counts": {s: len(rows) for s, rows in data.items()},
            "input_coverage": {s: [min(rows).isoformat(), max(rows).isoformat()]
                               for s, rows in data.items() if rows}}


def run_screen(root, h, feed, cache):
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "hypothesis-registry.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _run_screen(root, h, feed, cache)


def _run_screen(root, h, feed, cache):
    item = load_registry(root)["hypotheses"][h]
    status = item["status"]
    if status not in {"registered", "blocked"}:
        raise ValueError(f"{h} is {status}; completed rules cannot be screened again under the same ID")
    code = sha(root)
    registration = verify_registration(root, h, code_sha=code)
    path = REGISTRY.parent / h / "report.json"
    attempt = sum(t["status"] == "screening" for t in item["transitions"]) + 1
    attempt_path = path.parent / "attempts" / f"{attempt:02}" / "report.json"
    try:
        report = historical_screen(root, h, feed, cache)
    except ValueError as exc:
        report = {"id": h, "status": "blocked", "reason": str(exc), "primary": None, "checks": {}}
    except (httpx.HTTPError, OSError) as exc:
        code_label = f" (HTTP {exc.response.status_code})" if isinstance(exc, httpx.HTTPStatusError) else ""
        report = {"id": h, "status": "blocked", "reason": f"Historical source unavailable: {type(exc).__name__}{code_label}",
                  "primary": None, "checks": {}}
    report.update(id=h, phase="historical_screen", preregistration_commit=registration,
                  code_commit=code, broker_entries_enabled=False)
    transition(root, h, "screening", commit=code, path=attempt_path)
    save_report(root / attempt_path.parent, "report", report)
    save_report(root / path.parent, "report", report)
    transition(root, h, report["status"], commit=code, path=attempt_path, reason=report.get("reason"))
    return report


def archive_screen(root, h, paths):
    from app.research.replay.study import study

    item = load_registry(root)["hypotheses"][h]
    if item["status"] != "passed_phase1" or h not in {"S1", "S2", "S3"}:
        raise ValueError("Only historical intraday survivors can advance to archive replay")
    code = sha(root)
    verify_registration(root, h, code_sha=code)
    folder = REGISTRY.parent / h
    historical = json.loads((root / folder / "report.json").read_text())
    prior_effect = historical["primary"]["mean_bps"]
    report = study(root, ArchiveSource(paths), rule_for(root, h), archive=True)
    effect = (report.get("primary") or {}).get("mean_bps")
    if report.get("unresolved") or effect is None:
        status, reason = "blocked", "Arrival replay needs resolved candidate and control outcomes"
    elif effect > 0 and effect >= prior_effect * .5:
        status, reason = "forward", "Arrival replay meets the historical effect gate"
    else:
        status, reason = "killed", "Arrival replay effect did not meet the declared gate"
    report.update(status=status, reason=reason, phase="archive_replay",
                  code_commit=code, broker_entries_enabled=False)
    transition(root, h, "archive_replay", commit=code, path=folder / "archive-replay.json")
    save_report(root / folder, "archive-replay", report)
    transition(root, h, status, commit=code, path=folder / "archive-replay.json", reason=reason)
    return report


def show(report):
    print(json.dumps({k: report.get(k) for k in
                      ("id", "phase", "status", "reason", "primary", "checks")}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=cache_root())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    p = sub.add_parser("register")
    p.add_argument("id")
    p.add_argument("name")
    s = sub.add_parser("screen")
    s.add_argument("id", choices=["S1", "S2", "S3", "R1", "V1"])
    s.add_argument("--feed", default="iex", choices=["iex", "sip"])
    audit = sub.add_parser("audit-coverage", help="Find missing S1/S3 primary bars using only cached history")
    audit.add_argument("id", choices=["S1", "S3"])
    audit.add_argument("--feed", required=True, choices=["iex", "sip"])
    audit.add_argument("--output", required=True, type=Path)
    f = sub.add_parser("report")
    f.add_argument("id")
    for name in ("archive-replay", "forward"):
        a = sub.add_parser(name)
        a.add_argument("id")
        a.add_argument("archives", nargs="+", type=Path)
    a = sub.add_parser("calibrate")
    a.add_argument("archives", nargs="+", type=Path)
    sub.add_parser("probe")
    args = parser.parse_args()
    if args.command == "list":
        for key, value in load_registry(ROOT)["hypotheses"].items():
            print(f"{key}\t{value['status']}\t{value['name']}")
    elif args.command == "register":
        register(ROOT, args.id, args.name)
    elif args.command == "screen":
        show(run_screen(ROOT, args.id, args.feed, args.cache))
    elif args.command == "audit-coverage":
        from app.research.replay.study import coverage_audit

        rule = rule_for(ROOT, args.id)
        with AlpacaHistory(None, None, args.cache) as source:
            report = coverage_audit(intraday_source(source, rule, args.feed), rule, progress=progress)
        report.update(feed=args.feed, code_commit=sha(ROOT))
        json_write(args.output, report)
        print(json.dumps({k: v for k, v in report.items() if k != "gaps"}, sort_keys=True))
    elif args.command == "report":
        from app.hypotheses.registry import record_id
        print((ROOT / REGISTRY.parent / record_id(args.id) / "report.md").read_text(), end="")
    elif args.command == "archive-replay":
        show(archive_screen(ROOT, args.id, args.archives))
    elif args.command == "forward":
        state = load_registry(ROOT)["hypotheses"][args.id]["status"]
        raise ValueError(f"Forward runner requires WP5 after a survivor passes archive replay (current: {state})")
    elif args.command == "calibrate":
        sessions = ArchiveSource(args.archives)
        parity_file = next(p for p in args.archives if p.name.startswith("2026-11-20"))
        parity_count = frozen_parity(ROOT, ArchiveSource.read(parity_file))
        randomized = random_entry_rejection(sessions.sessions())
        if randomized["rejection_rate"] < .95:
            raise ValueError("Random calibration rejection below 95%")
        print(json.dumps({"frozen_signals_reproduced": parity_count, "random_entry": randomized}, sort_keys=True))
    elif args.command == "probe":
        with history(args.cache) as source:
            if not source.authorized:
                raise ValueError("No existing Alpaca credentials in the environment")
            for feed in ("sip", "iex"):
                try:
                    panel = source.bars(["SPY"], date(2026, 11, 20), date(2026, 11, 21), feed=feed)
                    print(feed, len(panel["SPY"]))
                except httpx.HTTPStatusError as exc:
                    print(feed, "unavailable", exc.response.status_code)


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
