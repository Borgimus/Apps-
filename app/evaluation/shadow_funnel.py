"""Count entry-filter failures once per existing shadow episode, with overlap."""
from collections import Counter, defaultdict


def rejection_breakdown(events):
    """Use original-direction signals; inverted/exit variants are alternatives.

    Initial rejection counts describe the first recorded observation per setup.
    Later eligibility is reported separately so a recovered setup is not lost.
    Unknown historical eligibility is never silently classified as a rejection.
    """
    signals = [e for e in events if e.get("event") == "signal"
               and e.get("variant", "baseline") == "baseline"]
    groups = defaultdict(list)
    missing_ids = 0
    for event in signals:
        if not event.get("opportunity_id"):
            missing_ids += 1
            continue
        groups[(event.get("session_id"), event["opportunity_id"])].append(event)
    anchors = {(e.get("session_id"), e.get("opportunity_id")) for e in events
               if e.get("event") == "eligible_entry" and e.get("opportunity_id")}
    setups = []
    for key, records in groups.items():
        first = records[0]
        initial = first.get("entry_filter_eligible")
        reasons = set(first.get("entry_filter_reasons") or []) if initial is False else set()
        if initial is False and not reasons:
            reasons.add("unrecorded_rejection_reason")
        setups.append({
            "session_id": key[0], "opportunity_id": key[1],
            "strategy_id": first.get("strategy_id", "unrecorded"),
            "symbol": first.get("symbol", "unrecorded"),
            "direction": first.get("direction", "unrecorded"),
            "observations": len(records), "first_observed_at": first.get("ts"),
            "initial_filter_status": "passed" if initial is True else "rejected" if initial is False else "unrecorded",
            "initial_reasons": sorted(reasons),
            "observed_reasons": sorted({r for e in records for r in (e.get("entry_filter_reasons") or [])}),
            "ever_passed_entry_filters": any(e.get("entry_filter_eligible") is True for e in records),
            "eligible_anchor_recorded": key in anchors,
            "became_eligible_after_rejection": initial is False and key in anchors,
        })

    def totals(rows):
        rejected = [r for r in rows if r["initial_filter_status"] == "rejected"]
        combinations = Counter(tuple(r["initial_reasons"]) for r in rejected)
        return {
            "unique_setups": len(rows),
            "initially_passed": sum(r["initial_filter_status"] == "passed" for r in rows),
            "initially_rejected": len(rejected),
            "initial_eligibility_unrecorded": sum(r["initial_filter_status"] == "unrecorded" for r in rows),
            "ever_passed_entry_filters": sum(r["ever_passed_entry_filters"] for r in rows),
            "eligible_anchors": sum(r["eligible_anchor_recorded"] for r in rows),
            "became_eligible_after_rejection": sum(r["became_eligible_after_rejection"] for r in rows),
            "initial_multiple_blockers": sum(len(r["initial_reasons"]) > 1 for r in rejected),
            "initial_rejections_by_reason": dict(sorted(Counter(
                reason for r in rejected for reason in r["initial_reasons"]).items())),
            "initial_rejection_combinations": [
                {"reasons": list(reasons), "setups": count}
                for reasons, count in sorted(combinations.items(), key=lambda item: (-item[1], item[0]))
            ],
        }

    by_strategy = defaultdict(list)
    for row in setups:
        by_strategy[row["strategy_id"]].append(row)
    return {
        "status": "available" if groups else "unavailable",
        "count_basis": "session_id + existing 60-minute shadow opportunity_id; original direction only",
        "overlap_note": "Reason counts overlap. Each combination counts a setup once at its first observation.",
        "eligibility_note": "Passing entry filters alone does not prove quote evidence, a fill, or portfolio admission.",
        "signal_observations": len(signals), "observations_without_setup_id": missing_ids,
        "anchors_without_recorded_signals": len(anchors - groups.keys()),
        **totals(setups), "by_strategy": {s: totals(rows) for s, rows in sorted(by_strategy.items())},
        "setups": setups,
    }
