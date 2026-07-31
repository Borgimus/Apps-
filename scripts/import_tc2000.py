#!/usr/bin/env python3
"""CLI importer for the three TC2000 EasyScan symbol files (atomic batch).

Usage:
    python scripts/import_tc2000.py \
        strength_1m_2026-07-31.txt strength_3m_2026-07-31.txt strength_6m_2026-07-31.txt \
        [--market-date YYYY-MM-DD] [--json]

Validates the batch (presence of all three, consistent date, symbol format, duplicates,
empty lists, freshness), then prints the candidate sets. This does NOT place any order and
does NOT connect to a broker — it is the deterministic import step only. Persistence to the
database arrives in Phase 3 (see docs/implementation_plan.md).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

# Make ``src`` importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tc2000.importer import ImportError_, build_batch  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Import three TC2000 EasyScan files as one batch.")
    ap.add_argument("files", nargs=3, help="the three strength_{1m,3m,6m}_YYYY-MM-DD.(txt|csv) files")
    ap.add_argument("--market-date", default=None, help="current market date (default: today)")
    ap.add_argument("--max-age-days", type=int, default=1)
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args(argv)

    raw: dict[str, str] = {}
    for f in args.files:
        p = Path(f)
        if not p.exists():
            print(f"error: file not found: {f}", file=sys.stderr)
            return 2
        raw[p.name] = p.read_text(encoding="utf-8")

    cur = date.fromisoformat(args.market_date) if args.market_date else date.today()

    try:
        batch = build_batch(raw, current_market_date=cur, max_age_days=args.max_age_days)
    except ImportError_ as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 1

    payload = {
        "status": batch.status,
        "market_date": batch.market_date.isoformat(),
        "batch_hash": batch.batch_hash,
        "candidate_sets": batch.candidate_sets,
        "counts": {k: len(v) for k, v in batch.candidate_sets.items()},
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"ACCEPTED batch for {payload['market_date']}  (hash {batch.batch_hash[:12]}…)")
        for name, syms in batch.candidate_sets.items():
            tag = " [ORDERS]" if name == "intersection_3_of_3" else " [shadow only]"
            print(f"  {name}{tag}: {len(syms)}  {', '.join(syms) if syms else '(none)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
