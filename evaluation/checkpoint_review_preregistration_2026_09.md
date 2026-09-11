# Pre-registered items for the guardrails_v3 checkpoint review

Declared: 2026-09-11, during the `guardrails_v3_tradier_options_2026_09_08`
observation cohort (progress at declaration: 2/5 completed scheduled sessions,
2/10 first-eligible opportunities). Declaring these now, before the checkpoint
data exists, prevents post-hoc rationalization at the review.

Nothing in this document activates a strategy, changes a live setting, or
modifies the running cohort. The checkpoint itself remains review-only.

## Item 1 — Shadow premium-budget sensitivity (pre-registered analysis)

### Evidence motivating it

The 2026-09-11 report's entry-filter rejection breakdown (first session with
this instrumentation): 13 unique shadow setups, 1 initially passed. Initial
blockers (overlapping — do not sum): `premium_budget_or_price_invalid` **8**,
`market_regime_mismatch` 6, `signal_quality_below_min` 3, `symbol_disabled` 2,
`contract_filters_unverified` 1.

If ~60% of setups stay budget-blocked, eligible opportunities accrue at ~1 per
session and the 10-opportunity checkpoint threshold takes ~2 more weeks. The
$250 budget is the dominant throttle on evidence collection.

### Pre-registered analysis (no live change)

At the checkpoint review, run the deterministic offline portfolio replay
(rollout doc 2026-09-09, change 4 — captured candidate and quote events,
captured risk rules) across budget variants, all other captured settings
unchanged:

- $250 (baseline, as captured)
- $500
- $1000

Report per variant: setups admitted, eligible anchors, sized and normalized
P&L per exit policy, and which blockers replace the budget blocker once budget
is lifted (a setup that then fails regime or quality filters is not budget
evidence). Label outputs as replay variants under distinct cohort labels;
never merge them with the live observation cohort or with each other.

### Decision rule (pre-registered)

If, across the 5 checkpoint sessions, `premium_budget_or_price_invalid` is an
initial blocker for ≥50% of unique setups AND the replay shows a raised budget
would have admitted ≥2x the eligible anchors, then declare a follow-on
amendment starting a parallel **shadow-only** cohort at the replay-supported
budget, under its own cohort label. Broker entry permissions stay `none`
regardless of this outcome.

## Item 2 — Reactivation sizing precondition (binding)

Whenever a future review authorizes broker entries again, in any cohort:

1. Sizing restarts at **1 contract per position** (`max_contracts_per_position: 1`)
   with the original one-contract premium budget, regardless of what any
   shadow or replay cohort used.
2. Scaled sizing may only be re-proposed after the new one-contract cohort has
   at least 30 broker-filled trades and a profit factor supporting scale-up,
   measured on that cohort alone.
3. Rationale on record: the 07-30 → 08-07 scaled cohort ($250/10-contract)
   produced -$238 over 20 trades (PF 0.74, max drawdown $458) after the
   one-contract population had shown only +$113 over 27 trades — scaling ran
   far ahead of the evidence. See `evaluation/one_contract_cohort_freeze.md`.

## Review checklist at checkpoint (5 sessions / 10 opportunities reached)

- [ ] Confirm all counted sessions were clean per the rollout's exclusion rules
      (no restarts, late starts, early ends, health errors, unpriced exits).
- [ ] Run Item 1 budget-sensitivity replay; apply its decision rule.
- [ ] Re-examine blocker distribution for non-budget dominant blockers.
- [ ] Verify Tradier vs Alpaca quote-evidence agreement on eligible anchors.
- [ ] Check reconciliation status in `one_contract_cohort_freeze.md`; broker
      fill reconciliation remains a precondition for any reactivation proposal.
- [ ] Any reactivation proposal must restate Item 2's sizing precondition.
