#!/usr/bin/env python3
"""Report one shadow-model version with diagnostic P&L and entry-filter coverage.

Version 2 is the default. Legacy version 1 requires explicit selection and has
known exit/fill defects. Unpriced outcomes are excluded from P&L. Neither this
report nor its entry-filter subset establishes a feasible portfolio or readiness.
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
from app.evaluation.shadow_book import SHADOW_MODEL_VERSION


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
    ap.add_argument("--model-version", default=SHADOW_MODEL_VERSION,
                    help="Report exactly one simulator version; use 1 for legacy diagnostics")
    args = ap.parse_args()

    path = Path(args.events)
    if not path.exists():
        print(f"No shadow book at {path}")
        return

    if args.model_version == "4":
        from app.evaluation.shadow_summary import read_events, session_date, summarize, to_markdown
        events = read_events(path, date=args.date)
        excluded = sum(str(e.get("model_version", "1")) != "4" for e in events)
        unpriced = sum(e.get("event") == "shadow_close" and e.get("shadow_pnl") is None
                       and str(e.get("model_version")) == "4"
                       and (not args.date or session_date(e) == args.date) for e in events)
        print(f"Model 4; excluded other-version events: {excluded}; unpriced closes: {unpriced}")
        dates = sorted({session_date(e) for e in events
                        if str(e.get("model_version")) == "4" and session_date(e)})
        if args.date:
            dates = [args.date]
        if not dates:
            print("No model-4 first-eligible evidence. Historical diagnostics require --model-version.")
        for day in dates:
            print(f"# Shadow evaluation: {day}\n")
            print(to_markdown(summarize(events, day)))
        return

    signals, closes = [], {}
    excluded_models = unpriced = unfilled = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if args.date and not str(rec.get("ts", "")).startswith(args.date):
            continue
        if str(rec.get("model_version", "1")) != args.model_version:
            excluded_models += 1
            continue
        if rec["event"] == "signal":
            signals.append(rec)
        elif rec["event"] == "shadow_close":
            if rec.get("shadow_pnl") is None:
                unpriced += 1
            else:
                closes[rec["signal_id"]] = rec
        elif rec["event"] == "shadow_unfilled":
            unfilled += 1

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
        variant = record.get("variant", "baseline")
        return strategy if variant == "baseline" else f"{strategy}:{variant}"

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
    print(f"Model {args.model_version}; excluded other-version events: {excluded_models}; "
          f"unfilled entries: {unfilled}; unpriced closes: {unpriced}")
    if args.model_version == "1":
        print("LEGACY MODEL: known exit, eligibility and fill-evidence defects. Historical diagnostics only.")
    print("All P&L is one-contract normalized diagnostic P&L. Portfolio constraints have not been replayed.")
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
        if ":" not in name
    )
    if total_blocked:
        vw = by_strategy.get("vwap_reclaim")
        if vw:
            print(f"\nDiagnostic block summary: {vw['blocked_opps']}/{total_blocked} "
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
        c for c in closes.values() if c.get("variant", "baseline") == "baseline"
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
    eligible = [c for c in baseline_closes if c.get("entry_filter_eligible") is True]
    print(f"\nEntry-filter-qualified baseline closes: {len(eligible)}, "
          f"normalized P&L {sum(c['shadow_pnl'] for c in eligible):+.2f}.")
    print("No capacity-change or strategy-reactivation gate is satisfied by this diagnostic report. "
          "Replay position limits, daily entries, cooldown, loss gates and reconciliation before a portfolio comparison.")


if __name__ == "__main__":
    main()
