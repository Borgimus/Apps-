# One-contract clean cohort — freeze declaration

Declared: 2026-09-11. Corrected: 2026-09-30 (see "Corrections" at the end).
Scope: all Phase 3 one-contract broker-fill sessions, 2026-07-13 through 2026-07-28.
Status: **frozen**. No further sessions may be added to this cohort. Later cohorts
(paper_scaled_250_cap_10 and its guardrails variants, and any future reactivation
cohort) are separate populations and must never be pooled with it.

## Why this declaration exists

Three records described the one-contract era with different boundaries:

| Record | Coverage | State before this declaration |
| --- | --- | --- |
| `phase3_tracking.json` cohort table | S1–S6 (07-13 → 07-20) | Sessions after 07-20 never folded in |
| `evaluation/ledger.json` | through 07-28 | Includes all 11 one-contract sessions |
| Daily reports (Sept format) | n/a | Refer to a "frozen one-contract clean cohort" without pinning its boundary |

This document pins the boundary. The full one-contract population is **11
sessions**: 07-13, 14, 15, 16, 17, 20, 22, 23, 24, 27, 28. The tracker's S1–S6
table is a correctly tracked sub-cohort; S7–S11 below were recorded as raw
artifacts and ledger rows but never given tracker entries.

## Sessions after the tracked S6 boundary (from per-session health reports)

| # | Date | Closed | W/L/BE | Realized P&L | Stale cancels | API errors |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| S7 | 2026-07-22 | 3 | 1/2/0 | -$10.00 | 0 | 0 |
| S8 | 2026-07-23 | 1 | 0/1/0 | -$3.00 | 0 | 0 |
| S9 | 2026-07-24 | 3 | 3/0/0 | +$37.00 | 0 | 0 |
| S10 | 2026-07-27 | 3 | 1/1/1 | +$3.00 | 0 | 0 |
| S11 | 2026-07-28 | 3 | 2/1/0 | +$3.00 | 2 | 0 |

S10's QQQ 677P closed at exactly its entry price ($0.23 → $0.23). The health
report of that date counted it as a loss because its reporter then classified
P&L ≤ 0 as a loss; it is a breakeven.

## Frozen cohort totals (subject to broker reconciliation below)

- Tracked sub-cohort S1–S6: 14 trades, 5W/9L, **+$83.00**, profit factor 1.51.
  No health or evaluation report survives for these six dates (only the 07-17
  loss is explained, by a container snapshot revert); these figures rest on
  `ledger.json` and `phase3_tracking.json` alone.
- S7–S11: 13 trades, 7W/5L/1BE, **+$30.00**.
- Full one-contract population: **27 trades, 12W/14L/1BE, +$113.00 net,
  profit factor 1.64** (gross wins $293 / gross losses $179).
- `ledger.json` by-strategy rows sum to 26 trades / +$113 (vwap_reclaim +$153,
  orb -$40); one filled trade carries no strategy attribution.

Sample-size caveat: 27 trades is below the 30-trade minimum the daily reports
themselves require, and far below what a strategy claim needs. This freeze
records data provenance; it is not evidence of edge.

## Complete broker-fill history (best evidence)

| Cohort | Dates | Sessions | Fills | W/L/BE | Net | PF |
| --- | --- | ---: | ---: | --- | ---: | ---: |
| pre-Phase-3 (contaminated) | 05-11 → 07-10 | 23 | 35 | 8/25/1 | -$398.50 | n/a |
| **one-contract (this cohort)** | 07-13 → 07-28 | 11 | 27 | 12/14/1 | **+$113.00** | 1.64 |
| unassigned | 07-29 | 1 | 1 | 0/1/0 | -$9.00 | 0.00 |
| `paper_scaled_250_cap_10` | 07-30 → 08-07 | 7 | 20 | 8/12/0 | -$238.00 | 0.74 |
| `paper_scaled_250_cap_10.guardrails_v2` | 08-11 → 08-26 | 12 | 23 | 8/14/1 | **-$726.00** | **0.20** |
| `…guardrails_v3_shadow_validation` | 08-27 → 09-08 | 7 | 0 | — | $0.00 | — |
| `guardrails_v3_tradier_options_2026_09_08` | 09-09 → 09-29 | 13 | 0 | — | $0.00 | — |
| **Total** | 05-11 → 09-29 | 74 | **106** | 36/66/3 | **≈ -$1,258.50** | — |

Well-attested post-boundary total (07-13 → 08-26, where ledger and health
reports agree): **71 fills, 28W/41L/2BE, -$860.00, PF 0.569.**

The pre-Phase-3 figure cannot be pinned down: `phase2_tracking.json` reports
+$126.00 over 27 trades for the same era, `ledger.json` -$602.50, and the
best-evidence reading -$398.50. The two records differ by $728.50 and have never
been reconciled. 2026-06-04 alone carries a $204 journal-price defect (Bug D)
that §6.1 of the protocol required correcting and that remains uncorrected.

The 07-29 session (one fill, -$9.00) falls between this cohort's boundary and
the scaled cohort's start and belongs to no ledger or tracker.

## Outstanding reconciliation (required before citing these numbers as final)

Per `evaluation/repair_rollout_2026_09.md` ("Work still needed"), items still open:

1. Obtain the VPS SQLite backup (`/root/trader` on the Debian VPS) and the
   Alpaca paper broker fill export covering 2026-05-11 → 2026-08-26.
2. Rebuild every cohort above separately from broker fills; verify entry/exit
   order IDs, quantities and prices row by row, including 07-29.
3. Record discrepancies in this file under a "Reconciliation results" heading;
   until then every figure above carries the label *unreconciled-against-broker*.

## Corrections (2026-09-30)

The 2026-09-11 version of this document:

1. Called the population "13 sessions" and then listed 11. It is 11.
2. Split the population 12W/15L, counting S10's breakeven as a loss.
3. Listed only the 07-30 → 08-07 scaled cohort (-$238) and jumped to 09-08,
   omitting the 08-11 → 08-26 `guardrails_v2` cohort (23 fills, -$726) and the
   08-27 → 09-08 cohort. It understated scaled-sizing losses by a factor of four.
4. Omitted the unassigned 07-29 fill.
5. Existed only on `claude/options-trading-research-system-TIU0p`, while 45 daily
   reports on the operational branch cited it. It now lives with the data.
