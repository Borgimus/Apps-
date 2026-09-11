# One-contract clean cohort — freeze declaration

Declared: 2026-09-11.
Scope: all Phase 3 one-contract broker-fill sessions, 2026-07-13 through 2026-07-28.
Status: **frozen**. No further sessions may be added to this cohort. Later cohorts
(paper_scaled_250_cap_10 and its guardrails variants, and any future reactivation
cohort) are separate populations and must never be pooled with it.

## Why this declaration exists

Three records described the one-contract era with different boundaries:

| Record | Coverage | State before this declaration |
| --- | --- | --- |
| `phase3_tracking.json` cohort table | S1–S6 (07-13 → 07-20) | Sessions after 07-20 never folded in |
| `evaluation/ledger.json` | through 07-28 | Includes all 13 one-contract sessions |
| Daily reports (Sept format) | n/a | Refer to a "frozen one-contract clean cohort" without pinning its boundary |

This document pins the boundary. The full one-contract population is 13 sessions
(07-13, 14, 15, 16, 17, 20, 22, 23, 24, 27, 28 — 11 trading days with sessions;
07-18/19 and 07-25/26 were weekends, 07-21 had no session). The tracker's S1–S6
table is a correctly-tracked sub-cohort; the S7–S13 sessions below were recorded
as raw artifacts and ledger rows but never given tracker entries.

## Sessions after the tracked S6 boundary (from per-session health reports)

| # | Date | Closed | W/L | Realized P&L | Stale cancels | API errors |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| S7 | 2026-07-22 | 3 | 1/2 | -$10.00 | 0 | 0 |
| S8 | 2026-07-23 | 1 | 0/1 | -$3.00 | 0 | 0 |
| S9 | 2026-07-24 | 3 | 3/0 | +$37.00 | 0 | 0 |
| S10 | 2026-07-27 | 3 | 1/2 | +$3.00 | 0 | 0 |
| S11 | 2026-07-28 | 3 | 2/1 | +$3.00 | 2 | 0 |

(S12/S13 do not exist; the numbering S1–S13 in earlier discussion overcounted.
Eleven sessions total: S1–S6 tracked, S7–S11 above.)

## Frozen cohort totals (subject to broker reconciliation below)

- Tracked sub-cohort S1–S6: 14 trades, 5W/9L, **+$83.00**, profit factor 1.51.
- S7–S11 add 13 trades, 7W/6L, **+$30.00**.
- Full one-contract population: **27 trades, 12W/15L, +$113.00 net.**
- Cross-check: `ledger.json` `phase3_cumulative.total_pnl` = 113.0 (matches) and
  by-strategy rows sum to 26 trades / +$113 (vwap_reclaim +$153, orb -$40).
- Known defect: that same ledger block reports `total_trades: 352` and
  `win_rate: 0.0341` — it counted non-trade rows. Ledger v3 (September repair
  train) fixes the derivation; the historical block is left as-is here and
  labelled unreliable. Use the per-session health reports for counts.

Sample-size caveat: 27 trades is below the 30-trade minimum the daily reports
themselves require, and far below what a strategy claim needs. This freeze
records data provenance; it is not evidence of edge.

## Outstanding reconciliation (required before citing these numbers as final)

Per `evaluation/repair_rollout_2026_09.md` ("Work still needed"), items still open:

1. Obtain the VPS SQLite backup (`/root/trader` on the Debian VPS via Termius)
   and the Alpaca paper broker fill export covering 2026-07-13 → 2026-08-07.
2. Rebuild the one-contract cohort and the scaled cohort separately from broker
   fills; verify entry/exit order IDs, quantities and prices row by row.
3. Record discrepancies in this file under a "Reconciliation results" heading;
   until then every figure above carries the label *unreconciled-against-broker*.

## Relationship to later cohorts

- 2026-07-30 → 2026-08-07: `paper_scaled_250_cap_10` (7 sessions, 20 trades,
  -$238, PF 0.74, max drawdown $458) — separate ledger, never pooled.
- 2026-09-08 →: `guardrails_v3_tradier_options_2026_09_08` observation cohort,
  zero broker entries by design. See
  `evaluation/checkpoint_review_preregistration_2026_09.md`.
