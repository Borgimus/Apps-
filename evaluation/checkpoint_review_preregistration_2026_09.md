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

### Reporting requirement added 2026-09-15: any-blocker vs sole-blocker

The blocker table overlaps: most budget-blocked setups also fail quality or
regime. The review must report two shares side by side and apply the ≥50%
threshold above to the first, while using the second to sanity-check the
replay's admitted count:

| Instrumented sessions | Budget among initial blockers | Budget the sole blocker |
| --- | ---: | ---: |
| 09-11, 09-14, 09-15 (45 unique setups) | 28 / 45 = 62% | 10 / 45 = 22% |

Lifting the budget alone frees only sole-blocker setups; the rest still fail
another filter. A replay that reports "≥2x eligible anchors" must therefore
show which blocker replaced budget for the setups that remain rejected.

## Item 3 — Exit-policy dead-band replay (pre-registered analysis)

Declared 2026-09-15 at 4/5 completed sessions and 5/10 eligible opportunities,
before any exit-policy conclusion has been drawn.

### Evidence motivating it

Maximum favorable / adverse excursion of every eligible anchor to date,
computed from the captured quote streams (164–223 quotes per anchor):

| Date | Anchor | Strategy | MFE | MAE | Exit | Sized P&L |
| --- | --- | --- | ---: | ---: | --- | ---: |
| 09-10 | IWM 290C | vwap_reclaim | +6.1% | -23.8% | session_end | -$36 |
| 09-11 | MARA 12C | orb | 0.0% | -30.4% | max_hold | -$39 |
| 09-14 | NVDA 220C | vwap_reclaim | -3.0% | -39.4% | max_hold | -$42 |
| 09-14 | IWM 288C | vwap_reclaim | +13.9% | -8.4% | eod_exit | +$11 |
| 09-15 | IWM 284P | orb | +1.8% | -13.5% | eod_exit | -$28 |

The trailing stop arms at +25%; no anchor reached +14%. The stop is -50%; no
anchor reached -40%. All five exits were clock exits, so the three live exit
variants (all triggered at +25%) have had nothing to act on, and every matched
pair shows a $0.00 difference by construction, not by result.

Base rates across the cohort's 49 fill-validated diagnostic setups: 29% reached
+25% at some point (median MFE +11.7%); 92% ended on time exits (26 max_hold,
19 EOD/session end), the -50% stop fired once, the trailing stop three times.
Zero of five eligible anchors reaching +25% has roughly an 18% probability
under that base rate and is not evidence about the entries. It is evidence
that the current exit policy is, in practice, a time-based exit.

### Pre-registered analysis (no live change)

At the checkpoint review, replay every eligible anchor's captured quote stream
under alternative exit rules, each applied to the same entry price, quantity
and fill time, with all other captured settings unchanged:

- trailing activation +10%, +15% (live: +25%), each with the live 25% trail
- stop loss -25%, -30% (live: -50%)
- max hold 60, 90 minutes (live: 120)

Report per variant, over the same anchors: sized and one-contract P&L, number
of price exits versus clock exits, and the per-anchor difference from the
live baseline. Label outputs as replay variants; never pool them with the live
variant tables or with each other. The live `breakeven_25` and
`partial_25_breakeven` simulations continue unchanged.

### Interpretation rule (pre-registered)

A replayed exit rule becomes a candidate for a future declared **shadow-only**
variant only if it improves sized P&L over the live baseline across at least
8 eligible anchors, with the improvement not concentrated in fewer than 3 of
them. A rule that merely converts clock exits into earlier clock exits, or
that wins on one outlier, is recorded and not adopted. Broker entry
permissions stay `none` regardless of this outcome.

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
- [ ] Run Item 1 budget-sensitivity replay; apply its decision rule, reporting
      any-blocker and sole-blocker budget shares side by side.
- [ ] Re-examine blocker distribution for non-budget dominant blockers.
- [ ] Run Item 3 exit-policy dead-band replay; apply its interpretation rule.
      Recompute the MFE/MAE table for all anchors through the checkpoint.
- [ ] Verify Tradier vs Alpaca quote-evidence agreement on eligible anchors.
- [ ] Check reconciliation status in `one_contract_cohort_freeze.md`; broker
      fill reconciliation remains a precondition for any reactivation proposal.
- [ ] Any reactivation proposal must restate Item 2's sizing precondition.
