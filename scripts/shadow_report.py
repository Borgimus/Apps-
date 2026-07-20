#!/usr/bin/env python3
"""
Shadow book report: per-strategy signal counts and outcomes, executed vs
capacity-blocked. Answers: is trade-slot competition starving a strategy
of samples?

Usage:
    python scripts/shadow_report.py [--events evaluation/shadow_book.jsonl] [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default="evaluation/shadow_book.jsonl")
    ap.add_argument("--date", default=None, help="Restrict to one session date (YYYY-MM-DD)")
    args = ap.parse_args()

    path = Path(args.events)
    if not path.exists():
        print(f"No shadow book at {path}")
        return

    signals, closes = [], {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if args.date and not str(rec.get("ts", "")).startswith(args.date):
            continue
        if rec["event"] == "signal":
            signals.append(rec)
        elif rec["event"] == "shadow_close":
            closes[rec["signal_id"]] = rec

    by_strategy: dict = defaultdict(lambda: {
        "executed": 0, "blocked": 0, "block_reasons": defaultdict(int),
        "shadow_trades": 0, "shadow_pnl": 0.0, "shadow_wins": 0,
    })
    for s in signals:
        st = by_strategy[s["strategy_id"]]
        if s["executed"]:
            st["executed"] += 1
        else:
            st["blocked"] += 1
            st["block_reasons"][s.get("block_reason") or "unknown"] += 1

    for c in closes.values():
        st = by_strategy[c["strategy_id"]]
        st["shadow_trades"] += 1
        st["shadow_pnl"] += c["shadow_pnl"]
        if c["shadow_pnl"] > 0:
            st["shadow_wins"] += 1

    scope = args.date or "all sessions"
    print(f"Shadow book report — {scope}\n")
    print(f"{'strategy':<14} {'qualified':>9} {'executed':>8} {'blocked':>7} "
          f"{'shadow N':>8} {'shadow PnL':>10}")
    for name, st in sorted(by_strategy.items()):
        total = st["executed"] + st["blocked"]
        print(f"{name:<14} {total:>9} {st['executed']:>8} {st['blocked']:>7} "
              f"{st['shadow_trades']:>8} {st['shadow_pnl']:>+10.2f}")
    print()
    for name, st in sorted(by_strategy.items()):
        if st["block_reasons"]:
            reasons = ", ".join(f"{r}={n}" for r, n in sorted(st["block_reasons"].items()))
            print(f"{name} block reasons: {reasons}")

    total_blocked = sum(st["blocked"] for st in by_strategy.values())
    if total_blocked:
        vw = by_strategy.get("vwap_reclaim", {})
        vb = vw.get("blocked", 0)
        print(f"\nSlot-competition check: {vb}/{total_blocked} blocked signals "
              f"were vwap_reclaim"
              + (f" (shadow PnL {vw['shadow_pnl']:+.2f} over {vw['shadow_trades']})"
                 if vw.get("shadow_trades") else ""))


if __name__ == "__main__":
    main()
