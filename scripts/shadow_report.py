#!/usr/bin/env python3
"""
Shadow book report: per-strategy signal counts and outcomes, executed vs
capacity-blocked. Answers: is trade-slot competition starving a strategy
of samples?

Reports BOTH raw qualified-signal events and unique trade opportunities
(episode-deduped), and splits shadow outcomes into fill-validated (primary
counterfactual) vs theoretical (sensitivity analysis).

Interpretation rule: shadow trades inform strategy design and capacity
decisions only — they never count toward the live-readiness paper sample.

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

    def _bucket():
        return {
            "raw": 0, "opportunities": 0, "executed_opps": 0, "blocked_opps": 0,
            "block_reasons": defaultdict(int),
            "validated_n": 0, "validated_pnl": 0.0, "validated_wins": 0,
            "theoretical_n": 0, "theoretical_pnl": 0.0,
        }

    by_strategy: dict = defaultdict(_bucket)
    # Opportunity-level rollup: first observation of each opportunity defines it;
    # an opportunity counts as executed if ANY of its observations executed.
    opp_seen: dict = {}
    for s in signals:
        st = by_strategy[s["strategy_id"]]
        st["raw"] += 1
        oid = s.get("opportunity_id") or s["signal_id"]
        if oid not in opp_seen:
            opp_seen[oid] = {"strategy": s["strategy_id"], "executed": False,
                             "block_reason": s.get("block_reason")}
            st["opportunities"] += 1
        if s["executed"]:
            opp_seen[oid]["executed"] = True

    for oid, info in opp_seen.items():
        st = by_strategy[info["strategy"]]
        if info["executed"]:
            st["executed_opps"] += 1
        else:
            st["blocked_opps"] += 1
            st["block_reasons"][info.get("block_reason") or "unknown"] += 1

    for c in closes.values():
        st = by_strategy[c["strategy_id"]]
        if c.get("fill_validated"):
            st["validated_n"] += 1
            st["validated_pnl"] += c["shadow_pnl"]
            if c["shadow_pnl"] > 0:
                st["validated_wins"] += 1
        else:
            st["theoretical_n"] += 1
            st["theoretical_pnl"] += c["shadow_pnl"]

    scope = args.date or "all sessions"
    print(f"Shadow book report — {scope}")
    print("(shadow results are design/capacity evidence only; NOT part of the "
          "live-readiness paper sample)\n")
    hdr = (f"{'strategy':<14} {'raw':>5} {'opps':>5} {'exec':>5} {'blocked':>7} "
           f"{'validN':>6} {'validPnL':>9} {'theoN':>6} {'theoPnL':>8}")
    print(hdr)
    for name, st in sorted(by_strategy.items()):
        print(f"{name:<14} {st['raw']:>5} {st['opportunities']:>5} "
              f"{st['executed_opps']:>5} {st['blocked_opps']:>7} "
              f"{st['validated_n']:>6} {st['validated_pnl']:>+9.2f} "
              f"{st['theoretical_n']:>6} {st['theoretical_pnl']:>+8.2f}")
    print()
    for name, st in sorted(by_strategy.items()):
        if st["block_reasons"]:
            reasons = ", ".join(f"{r}={n}" for r, n in sorted(st["block_reasons"].items()))
            print(f"{name} blocked-opportunity reasons: {reasons}")

    total_blocked = sum(st["blocked_opps"] for st in by_strategy.values())
    if total_blocked:
        vw = by_strategy.get("vwap_reclaim")
        if vw:
            print(f"\nSlot-competition check: {vw['blocked_opps']}/{total_blocked} "
                  f"blocked opportunities were vwap_reclaim | "
                  f"fill-validated: {vw['validated_n']} for {vw['validated_pnl']:+.2f} "
                  f"({vw['validated_wins']}W) | "
                  f"theoretical: {vw['theoretical_n']} for {vw['theoretical_pnl']:+.2f}")
    print(f"\nBaseline gate: >=10 unique capacity-blocked opportunities and >=5 "
          f"sessions before evaluating cap changes (current blocked opps: {total_blocked})")


if __name__ == "__main__":
    main()
