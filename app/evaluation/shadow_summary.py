"""Eligible-first summaries, matched comparisons and observation progress."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from app.evaluation.shadow_replay import ET, VARIANTS, replay_session
from app.evaluation.shadow_funnel import rejection_breakdown
from app.trading.quote_evidence import parse_quote_timestamp


def read_events(path, *, date=None) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    records = []
    progress_events = {"shadow_session_start", "shadow_session_end", "eligible_entry",
                       "shadow_close", "shadow_quote_error"}
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            event = json.loads(line)
            # Keep historical progress evidence, but avoid retaining months of
            # quote samples in memory on the 1 GB deployment host.
            if date and session_date(event) != date and event.get("event") not in progress_events:
                continue
            records.append(event)
    return records


def session_date(event):
    ts = parse_quote_timestamp(event.get("ts"))
    return ts.astimezone(ET).date().isoformat() if ts else None


def summarize(events, date, *, model_version="4", context=None) -> dict:
    selected = [e for e in events if session_date(e) == date
                and str(e.get("model_version", "1")) == str(model_version)]
    candidates = [e for e in selected if e.get("event") == "eligible_entry"]
    closes = [e for e in selected if e.get("event") == "shadow_close"]
    eligible = [e for e in closes if e.get("channel") == "eligible"]
    diagnostic = [e for e in closes if e.get("channel", "diagnostic") == "diagnostic"]
    starts = [e for e in selected if e.get("event") == "shadow_session_start"]
    identities = {tuple(r.get("session_context", {}).get(k) for k in
                        ("options_data_provider", "options_data_adapter_hash", "evaluation_cohort", "replay_policy_hash"))
                  for r in starts}
    if len(identities) > 1:
        raise ValueError("Mixed shadow cohorts or replay policies within the session")
    context = context or (starts[0]["session_context"] if starts else {})

    def totals(rows):
        priced = [r for r in rows if r.get("fill_validated") and r.get("shadow_pnl") is not None]
        sized = [r for r in priced if r.get("sized_shadow_pnl") is not None]
        return {"closed": len(rows), "priced": len(priced), "unpriced": len(rows) - len(priced),
                "sized_priced": len(sized),
                "sized_pnl": round(sum(r["sized_shadow_pnl"] for r in sized), 2) if sized else None,
                "one_contract_pnl": round(sum(r["shadow_pnl"] for r in priced), 2) if priced else None}

    pairs = defaultdict(dict)
    for row in eligible:
        pairs[row["pair_id"]][row["variant"]] = row
    comparisons = {}
    for variant in VARIANTS[1:]:
        matched = []
        for pair_id, rows in pairs.items():
            baseline, other = rows.get("baseline"), rows.get(variant)
            if not baseline or not other:
                continue
            identity = ("option_symbol", "entry_time", "entry_price", "quantity", "fill_validated_at")
            if any(baseline.get(k) != other.get(k) for k in identity):
                continue
            if any(r.get("sized_shadow_pnl") is None or not r.get("fill_validated") for r in (baseline, other)):
                continue
            matched.append({"pair_id": pair_id, "baseline_pnl": baseline["sized_shadow_pnl"],
                            "variant_pnl": other["sized_shadow_pnl"],
                            "difference": round(other["sized_shadow_pnl"] - baseline["sized_shadow_pnl"], 2)})
        comparisons[variant] = {
            "matched_pairs": len(matched), "pairs": matched,
            "unmatched_or_unpriced": len(candidates) - len(matched),
            "baseline_pnl": round(sum(p["baseline_pnl"] for p in matched), 2) if matched else None,
            "variant_pnl": round(sum(p["variant_pnl"] for p in matched), 2) if matched else None,
            "difference": round(sum(p["difference"] for p in matched), 2) if matched else None,
        }
    replay = {variant: replay_session(selected, variant, respect_permissions=False) for variant in VARIANTS}
    oversized = [r for r in diagnostic if (r.get("affordable_quantity") or 0) < 1]
    rejected = [r for r in diagnostic if r.get("entry_filter_eligible") is not True]
    result = {
        "model_version": str(model_version), "date": date, "counts_toward_readiness": False,
        "first_eligible_tracking": "available" if str(model_version) == "4" and starts else "unavailable",
        "eligible_opportunities": len(candidates),
        "rejection_breakdown": rejection_breakdown(selected),
        "exit_quote_evidence": [_exit_quote_evidence(r) for r in eligible if r["variant"] == "baseline"],
        "eligible_independent": {v: totals([r for r in eligible if r["variant"] == v]) for v in VARIANTS},
        "matched_exits": comparisons,
        "current_permissions_replay": replay_session(selected, respect_permissions=True),
        "research_portfolios": replay,
        "diagnostics": {
            "all": {"closed": len(diagnostic)}, "oversized": {"closed": len(oversized)},
            "rejected": {"closed": len(rejected)},
            "by_variant": {v: totals([r for r in diagnostic if r.get("variant", "baseline") == v])
                           for v in (*VARIANTS, "inverted")},
            "rejection_reasons": dict(Counter(reason for r in rejected for reason in r.get("entry_filter_reasons", []))),
            "aggregation_note": "Rejected and oversized subsets overlap. Variants are alternative scenarios.",
        },
        "observation_progress": observation_progress(events, context, through_date=date),
    }
    return result


def _exit_quote_evidence(row):
    quote_ts = parse_quote_timestamp(row.get("last_quote_timestamp"))
    exit_ts = parse_quote_timestamp(row.get("ts"))
    age = (exit_ts - quote_ts).total_seconds() if quote_ts and exit_ts else None
    return {"option_symbol": row.get("option_symbol"), "exit_time": row.get("ts"),
            "quote_timestamp": row.get("last_quote_timestamp"),
            "quote_age_seconds": round(age, 3) if age is not None else None,
            "final_quote_refresh": row.get("final_quote_refresh") or "not_requested",
            "outcome_priced": row.get("outcome_priced", row.get("shadow_pnl") is not None)}


def observation_progress(events, context, *, through_date):
    """Count completed scheduled sessions under identical controls and model."""
    identity = ("options_data_provider", "options_data_adapter_hash", "evaluation_cohort",
                "shadow_model_version", "replay_policy_hash", "broker_entry_strategies")
    targets = context.get("observation_review_targets", {"scheduled_sessions": 5, "eligible_opportunities": 10})
    sessions = defaultdict(list)
    for event in events:
        day = session_date(event)
        if day and day <= through_date:
            sessions[day].append(event)
    completed, exclusions, opportunities = [], {}, 0
    for day, records in sorted(sessions.items()):
        starts = [r for r in records if r.get("event") == "shadow_session_start"]
        if not starts or not any(all(r.get("session_context", {}).get(k) == context.get(k) for k in identity) for r in starts):
            continue
        ends = [r for r in records if r.get("event") == "shadow_session_end"]
        reasons = []
        if len(starts) != 1 or len(ends) != 1:
            reasons.append("restart_or_missing_completion")
        else:
            start, end = starts[0], ends[0]
            c = start["session_context"]
            if end.get("session_id") != start.get("session_id"):
                reasons.append("completion_session_mismatch")
            if c.get("broker_entry_strategies") != []:
                reasons.append("broker_strategies_enabled_or_unrecorded")
            delay = c.get("start_delay_seconds")
            if delay is None or delay > 60:
                reasons.append("late_or_unrecorded_start")
            end_ts = parse_quote_timestamp(end.get("ts")).astimezone(ET)
            hour, minute = map(int, c["replay_policy"]["eod_exit_time"].split(":"))
            scheduled_end = end_ts.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if end_ts < scheduled_end:
                reasons.append("early_end")
            if end.get("api_errors") != 0 or end.get("reconciliation_warnings") != 0:
                reasons.append("session_health_review_required")
            if any(r.get("event") == "shadow_quote_error" for r in records):
                reasons.append("shadow_quote_errors")
            if any(r.get("event") == "shadow_close" and r.get("channel") == "eligible"
                   and r.get("fill_validated") and not r.get("outcome_priced") for r in records):
                reasons.append("unpriced_eligible_exit")
        if reasons:
            exclusions[day] = reasons
        else:
            completed.append(day)
            opportunities += len({r["pair_id"] for r in records if r.get("event") == "eligible_entry"})
    return {"completed_scheduled_sessions": len(completed), "dates": completed,
            "eligible_opportunities": opportunities, "targets": targets, "excluded_dates": exclusions,
            "review_checkpoint_reached": len(completed) >= targets["scheduled_sessions"]
            and opportunities >= targets["eligible_opportunities"],
            "automatic_strategy_activation": False}


def to_markdown(summary):
    """Place eligible evidence first; keep independent and portfolio P&L distinct."""
    def money(value):
        return "unavailable" if value is None else f"${value:+.2f}"

    s = summary
    lines = ["## Eligible shadow evidence", "",
             f"First-eligible tracking: **{s['first_eligible_tracking']}**. "
             f"Eligible entry anchors: **{s['eligible_opportunities']}**.", "",
             "These are simulated quote-touch outcomes. They exclude fees, queue uncertainty "
             "and exit slippage and add no broker-fill readiness evidence.", "",
             "| Independent exit policy | Priced / closed | Sized P&L |", "| --- | ---: | ---: |"]
    for variant, value in s["eligible_independent"].items():
        lines.append(f"| {variant} | {value['priced']} / {value['closed']} | {money(value['sized_pnl'])} |")
    lines += ["", "Matched comparisons use identical contract, entry time, limit price, quantity and fill time.", "",
              "| Variant | Complete matched pairs | Baseline P&L | Variant P&L | Difference |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for variant, value in s["matched_exits"].items():
        lines.append(f"| {variant} | {value['matched_pairs']} | {money(value['baseline_pnl'])} | "
                     f"{money(value['variant_pnl'])} | {money(value['difference'])} |")
    lines += ["", "Partial exits require at least two contracts. Missing or unpriced pairs are excluded.", "",
              "### Chronological portfolio replay", "",
              "Each exit policy has an independent portfolio. Research portfolios assume strategy "
              "reactivation while applying the captured budget and risk rules. Actual permissions stay unchanged.", "",
              "| Scenario | Status | Admitted | Closed | Sized portfolio P&L |",
              "| --- | --- | ---: | ---: | ---: |"]
    for name, r in [("Current permissions", s["current_permissions_replay"]), *s["research_portfolios"].items()]:
        lines.append(f"| {name} | {r['status'].replace('_', ' ')} | {r.get('admitted', 'n/a')} | "
                     f"{r.get('closed', 'n/a')} | {money(r.get('portfolio_pnl'))} |")
    if s["research_portfolios"]["partial_25_breakeven"]["status"] == "not_executable":
        lines += ["", "Partial-exit replay was **not executable**: no eligible candidate supported two contracts. "
                  "There is no partial-exit P&L comparison."]
    evidence = s.get("exit_quote_evidence", [])
    if evidence:
        lines += ["", "### Eligible baseline exit quotes", "",
                  "| Contract | Quote age at exit | Final refresh | Priced |",
                  "| --- | ---: | --- | --- |"]
        for row in evidence:
            age = "unavailable" if row["quote_age_seconds"] is None else f"{row['quote_age_seconds']:.3f}s"
            lines.append(f"| {row['option_symbol']} | {age} | {row['final_quote_refresh'].replace('_', ' ')} | "
                         f"{'yes' if row['outcome_priced'] else 'no'} |")
    if s["first_eligible_tracking"] != "available":
        lines += ["", "Recorded events lack a complete first-eligible quote stream; no portfolio outcome is inferred."]
    progress = s["observation_progress"]
    lines += ["", "### Observation progress", "",
              f"Completed scheduled sessions: {progress['completed_scheduled_sessions']}/{progress['targets']['scheduled_sessions']}. "
              f"Eligible opportunities: {progress['eligible_opportunities']}/{progress['targets']['eligible_opportunities']}.",
              "The checkpoint requests a review only. It never activates a strategy."]
    funnel = s.get("rejection_breakdown", {})
    lines += ["", "### Entry-filter rejection breakdown", ""]
    if funnel.get("status") != "available":
        lines.append("Original-direction signal observations are unavailable; no rejection counts are inferred.")
    else:
        lines += [f"{funnel['signal_observations']} original-direction observations formed "
                  f"{funnel['unique_setups']} unique shadow setups using the existing 60-minute episode rule. "
                  "This denominator differs from the distinct signal timestamps in the trade summary.", "",
                  f"Initially passed filters: {funnel['initially_passed']}. "
                  f"Initially rejected: {funnel['initially_rejected']}. "
                  f"Initial eligibility unrecorded: {funnel['initial_eligibility_unrecorded']}.",
                  f"Ever passed filters: {funnel['ever_passed_entry_filters']}. "
                  f"Eligible anchors recorded: {funnel['eligible_anchors']}. "
                  f"Became eligible after rejection: {funnel['became_eligible_after_rejection']}.", "",
                  "| Initial blocker | Unique setups |", "| --- | ---: |"]
        for reason, count in funnel["initial_rejections_by_reason"].items():
            lines.append(f"| {reason} | {count} |")
        lines += ["", f"Setups with multiple initial blockers: {funnel['initial_multiple_blockers']}. "
                  "Reason counts overlap and must not be added together.", "",
                  "| Initial blocker combination | Unique setups |", "| --- | ---: |"]
        for combo in funnel["initial_rejection_combinations"]:
            lines.append(f"| {', '.join(combo['reasons'])} | {combo['setups']} |")
        lines += ["", funnel["eligibility_note"]]
        if funnel["observations_without_setup_id"] or funnel["anchors_without_recorded_signals"]:
            lines += [f"Coverage gaps: {funnel['observations_without_setup_id']} observations without setup IDs; "
                      f"{funnel['anchors_without_recorded_signals']} anchors without recorded signals."]
    lines += ["", "### Diagnostic setups", "",
              "These include failed entry filters and oversized contracts. P&L is one-contract normalized; "
              "it does not establish an executable portfolio. Alternative exit policies must not be added together.", "",
              "| Diagnostic variant | Priced / closed | Normalized P&L |", "| --- | ---: | ---: |"]
    for variant, value in s["diagnostics"]["by_variant"].items():
        lines.append(f"| {variant} | {value['priced']} / {value['closed']} | {money(value['one_contract_pnl'])} |")
    lines += ["", f"Oversized diagnostic closes: {s['diagnostics']['oversized']['closed']}. "
              f"Rejected diagnostic closes: {s['diagnostics']['rejected']['closed']}. These subsets overlap."]
    return "\n".join(lines) + "\n"
