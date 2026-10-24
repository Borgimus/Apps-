# Project Handoff

**Updated:** 2026-10-01. Replaces the 2026-07-10 Phase 2 handoff, which described
a state that ended months ago.

## What this is

An intraday options research system. It scans 38 liquid symbols, generates ORB,
VWAP-reclaim and RSI-trend signals, selects option contracts, and records
outcomes. Execution is Alpaca **paper** only; live trading cannot be enabled from
`config.yaml` and is refused by the broker factory and the risk manager.

Sessions run unattended on a Debian VPS from the branch
`agent/paper-scaled-sizing-250-10` (configurable as `TRADER_CODE_BRANCH`),
driven by `scripts/ops/` (preflight, start, close-out, watchdog, installer).

## Current state

| Item | State |
| --- | --- |
| Broker entries | **Suspended since 2026-08-27.** No strategy may submit an order; `rsi_trend` is diagnostic-only in every mode. |
| Options data | **None.** Tradier deactivated the account (2026-09-22). Every request returns 401. |
| Observation cohort `guardrails_v3_tradier_options_2026_09_08` | **Closed** at 6 valid sessions and 6 of 10 eligible opportunities. See the outcome addendum in `evaluation/checkpoint_review_preregistration_2026_09.md`. |
| Sessions | Should not run until a provider is chosen. With the current release they fail preflight at the options-data canary. |

## What the evidence says

All figures are unreconciled against broker fill exports (see the freeze doc).

- **106 broker fills, 2026-05-11 → 08-26, roughly -$1,258.** The well-attested
  post-boundary period (07-13 → 08-26) is **71 fills, -$860, profit factor 0.57**.
- The only positive cohort is the one-contract cohort: 27 fills, +$113 (half its
  sessions have no surviving health report). Both scaled-sizing cohorts lost:
  -$238 (PF 0.74) and -$726 (PF 0.20).
- Current-methodology shadow outcomes (64 fill-validated closes, 09-10 → 09-21):
  PF 0.57, mean -0.058 R, P(mean > 0) ≈ 3%. Trading the inverse signal lost 86%
  less. No take-profit/stop combination in a 24-cell grid was profitable.
- ORB signals do not predict the underlying: direction-adjusted forward returns
  of -0.04% / -0.10% / -0.09% at 5 / 15 / 30 minutes over 25 signals.

Authoritative record: `evaluation/one_contract_cohort_freeze.md` (all cohorts,
corrections, outstanding reconciliation).

## Releases in flight

| PR | Content | How it ships |
| --- | --- | --- |
| #28 | PR #27's operations layer plus data-health, reporting, safety and ops fixes | Operations installer, after the host steps in `evaluation/data_health_rollout_2026_09_30.md`. **Never merge remotely.** |
| #29 | Alpaca options adapter repairs for an OPRA feed | Only with a new cohort declaration: `evaluation/alpaca_options_provider_rollout.md` |
| Signal and gate corrections | Quality-score and risk-gate fixes that change evidence semantics | With the new cohort; see its PR |

## Decisions waiting on the owner

1. Stop the session cron (until a provider is chosen).
2. Configure and test the ntfy alert channel (`OPS_NTFY_URL`, `session_ops.py test-alert`).
3. Choose an options-data provider. Alpaca OPRA is the prepared path; Basic
   (indicative) cannot validate fills under the existing evidence rules.
4. Choose the next hypothesis. Screen it on underlying price history first:
   it must show a positive direction-adjusted forward return on the stock before
   any option is selected. Keep ORB and VWAP-reclaim as the unchanged baseline.
5. Reconcile historical cohorts against the VPS database and an Alpaca fill export.

## Permanent rules

- `LIVE_TRADING_ENABLED=false`. Paper evaluation only.
- Broker-reported fills are authoritative over journal prices.
- Cohorts are never pooled. A provider, adapter or policy change starts a new
  cohort with a declared baseline.
- Parameter changes need a written protocol, not an amendment block appended to
  `phase3_tracking.json`.
- A session that produced no usable data is degraded and excluded, whatever its
  error counters say.
- `scan_results` rows for 2026-06-24 and 2026-06-25 are sentinel data from a
  total data failure and are excluded from all diagnostics.

## Where things are

| Path | Purpose |
| --- | --- |
| `scripts/session_runner.py` | Session loop |
| `scripts/ops/` | Preflight, start, close-out, watchdog, installer |
| `scripts/session_ops.py` | Ops CLI: watchdog, calendar, alerts, options-data canary |
| `app/operations/provider_health.py` | Options-data metering, authorization breaker, session verdict |
| `app/evaluation/` | Shadow book, replay, observation progress, daily report |
| `app/api/eod_review.py` | End-of-day review page |
| `evaluation/` | Ledgers, protocols, rollouts, freeze doc, reports |
