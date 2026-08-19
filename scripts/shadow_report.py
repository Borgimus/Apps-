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
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.evaluation.trade_attribution import analyze_trade, summarize_diagnostics


def _diagnostic(close: dict):
    """Adapt a shadow close event to the shared trade diagnostic schema."""
    return analyze_trade(SimpleNamespace(
        id=None,
        strategy_id=close.get("strategy_id"),
        underlying_symbol=close.get("symbol"),
        option_symbol=close.get("option_symbol"),
        signal_direction=close.get("direction"),
        entry_time=close.get("entry_time"),
        expiration=None,
        fill_price=close.get("entry_price"),
        exit_price=close.get("exit_price"),
        quantity=1,
        filled_quantity=1,
        realized_pnl=close.get("shadow_pnl"),
        mfe=close.get("mfe"),
        mae=close.get("mae"),
        peak_price=close.get("peak_price"),
        trough_price=close.get("trough_price"),
        spread_pct=None,
        delta=None,
        time_to_fill_secs=None,
        exit_reason=close.get("exit_reason"),
        contract_metadata_expected=False,
    ))


def _print_excursion_summary(label: str, rows: list) -> None:
    summary = summarize_diagnostics(rows)
    if not rows:
        print(f"{label}: no closed priceable opportunities")
        return
    counts = Counter(row.primary_attribution for row in rows)
    count_text = ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
    avg_mfe = summary["average_mfe_dollars"]
    avg_mae = summary["average_mae_dollars"]
    coverage = summary["coverage_pct"]
    avg_mfe_text = f"{avg_mfe:+.2f}" if avg_mfe is not None else "n/a"
    avg_mae_text = f"{avg_mae:+.2f}" if avg_mae is not None else "n/a"
    print(
        f"{label}: coverage {summary['trades_with_excursion_data']}/"
        f"{summary['trades_analyzed']} ({coverage:.0%}) | "
        f"avg MFE {avg_mfe_text} | avg MAE {avg_mae_text} | "
        f"MFE giveback {summary['total_mfe_giveback_dollars']:+.2f} | "
        f"dominant {summary['dominant_failure_mode'] or 'none'}"
    )
    print(f"{label} attribution: {count_text}")


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
    def _strategy_label(record: dict) -> str:
        strategy = record["strategy_id"]
        return (
            f"{strategy}:inverted"
            if record.get("variant") == "inverted"
            else strategy
        )

    # Opportunity-level rollup: first observation of each opportunity defines it;
    # an opportunity counts as executed if ANY of its observations executed.
    opp_seen: dict = {}
    for s in signals:
        label = _strategy_label(s)
        st = by_strategy[label]
        st["raw"] += 1
        oid = s.get("opportunity_id") or s["signal_id"]
        if oid not in opp_seen:
            opp_seen[oid] = {"strategy": label, "executed": False,
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
        st = by_strategy[_strategy_label(c)]
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

    # Inverted rows are counterfactual outcomes of the same opportunities.
    # Exclude them from the baseline slot-competition denominator.
    total_blocked = sum(
        st["blocked_opps"]
        for name, st in by_strategy.items()
        if not name.endswith(":inverted")
    )
    if total_blocked:
        vw = by_strategy.get("vwap_reclaim")
        if vw:
            print(f"\nSlot-competition check: {vw['blocked_opps']}/{total_blocked} "
                  f"blocked opportunities were vwap_reclaim | "
                  f"fill-validated: {vw['validated_n']} for {vw['validated_pnl']:+.2f} "
                  f"({vw['validated_wins']}W) | "
                  f"theoretical: {vw['theoretical_n']} for {vw['theoretical_pnl']:+.2f}")

    inverse = [c for c in closes.values() if c.get("variant") == "inverted"]
    if inverse:
        validated = [c for c in inverse if c.get("fill_validated")]
        theoretical = [c for c in inverse if not c.get("fill_validated")]
        print(
            "\nInverted-direction comparison: "
            f"fill-validated {len(validated)} for "
            f"{sum(c['shadow_pnl'] for c in validated):+.2f}; "
            f"theoretical {len(theoretical)} for "
            f"{sum(c['shadow_pnl'] for c in theoretical):+.2f}"
        )

    baseline_closes = [
        c for c in closes.values() if c.get("variant") != "inverted"
    ]
    validated_diagnostics = [
        _diagnostic(c) for c in baseline_closes if c.get("fill_validated")
    ]
    theoretical_diagnostics = [
        _diagnostic(c) for c in baseline_closes if not c.get("fill_validated")
    ]
    print("\nExcursion and failure attribution")
    print("(poll-sampled option-price extrema; attribution is diagnostic only)")
    _print_excursion_summary("Fill-validated", validated_diagnostics)
    _print_excursion_summary("Theoretical", theoretical_diagnostics)
    print(f"\nBaseline gate: >=10 unique capacity-blocked opportunities and >=5 "
          f"sessions before evaluating cap changes (current blocked opps: {total_blocked})")


if __name__ == "__main__":
    main()
