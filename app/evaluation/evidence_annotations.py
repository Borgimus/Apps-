"""Reporting annotations for explicit historical reviews. Never rewrite evidence."""
from __future__ import annotations

import math


def health_review(context):
    identity = {
        "runner_started_at": "2026-09-11T09:30:11.623824-04:00",
        "options_data_provider": "tradier",
        "options_data_adapter_hash": "567d1f77",
        "evaluation_cohort": "guardrails_v3_tradier_options_2026_09_08",
        "shadow_model_version": "4",
        "replay_policy_hash": "7ffc555cb32513d5",
    }
    if all(context.get(k) == value for k, value in identity.items()):
        return {
            "date": "2026-09-11", "disposition": "accepted_after_review",
            "outage_free": False,
            "reference": "evaluation/data_feed_error_accounting_amendment_2026_09_14.md",
            "reason": "Equity-data outage after entry cutoff and after the eligible MARA exit. "
                      "Existing amendment retains checkpoint credit and the individual outcome.",
        }
    return None


def exit_trigger_coverage(eligible, context):
    """Describe observed triggers without claiming unobserved quote-path behavior."""
    baselines = {r["pair_id"]: r for r in eligible
                 if r.get("variant") == "baseline" and r.get("pair_id")}
    threshold = context.get("replay_policy", {}).get("variant_trigger_pct")
    threshold_valid = isinstance(threshold, (float, int)) and math.isfinite(threshold) and threshold > 0
    rows = []
    for pair, row in baselines.items():
        priced = row.get("fill_validated") is True and row.get("sized_shadow_pnl") is not None
        entry, peak = row.get("entry_price"), row.get("peak_price")
        known = (priced and threshold_valid and all(isinstance(x, (int, float)) and math.isfinite(x)
                                                  for x in (entry, peak)) and entry > 0)
        gain = (peak / entry - 1) if known else None
        rows.append({"pair_id": pair, "quantity": row.get("quantity"), "peak_gain": gain,
                     "threshold_reached": gain + 1e-12 >= threshold if known else None})
    executable = [r for r in rows if isinstance(r["quantity"], (int, float)) and r["quantity"] >= 2]
    def counts(values):
        return {"opportunities": len(values),
                "known_peak_paths": sum(r["threshold_reached"] is not None for r in values),
                "observed_threshold_reached": sum(r["threshold_reached"] is True for r in values),
                "unknown_peak_paths": sum(r["threshold_reached"] is None for r in values)}
    return {"threshold_pct": threshold if threshold_valid else None,
            "baseline": counts(rows), "partial_executable": counts(executable),
            "partial_ineligible_or_quantity_unknown": len(rows) - len(executable),
            "variant_actions": {
                variant: {"closes": len(values),
                          "breakeven_armed": sum(r.get("breakeven_armed") is True for r in values),
                          "partial_taken": sum(r.get("partial_taken") is True for r in values)}
                for variant in ("breakeven_25", "partial_25_breakeven")
                for values in [[r for r in eligible if r.get("variant") == variant]]},
            "note": "Observed peak coverage only. Identical P&L with no threshold crossings does not compare "
                    "post-trigger exit quality. Partial exits need at least two contracts."}
