# Data-health, reporting and operations release (2026-09-30)

Status: **published for review, not deployed.** Deploy through the operations
installer described in `evaluation/operations_rollout_2026_09_21.md`; do not
merge this branch remotely. A remote merge makes the VPS checkout differ from
its branch, and preflight then refuses every session (this is how 2026-09-16
and 09-17 were lost).

This release contains PR #27 (rebuilt on the 2026-09-29 head, code unchanged)
plus the fixes below. It changes no frozen file (`config.yaml`,
`requirements.lock`, `config/ticker_universe.yaml`, `app/config/settings.py`,
`evaluation/phase3_tracking.json`) and no broker adapter, so the installer
accepts it. Strategy permissions are unchanged: no strategy may submit a broker
entry. Validation: full suite with no exclusions, **1059 passed, 2 skipped**.

## Why

From 2026-09-22 Tradier rejected every options request with 401 (the account
was deactivated). Six sessions ran to completion, reported `api_errors: 0` and
`data_feed_errors: 0`, passed preflight, and were counted as completed
observation. The same failure class would have left open positions without a
working stop loss.

## What changes

**Safety (fail closed)**
- An open position with no usable quote keeps its last valid mark instead of
  being marked flat at entry; time exits still apply; after 120 s unpriced it
  is closed as a mandatory `quote_unavailable` exit.
- Unknown earnings status blocks broker entries (observation continues).
- The stale-pending-order pre-flight check fails when it cannot run.

**Provider health**
- Every options-data call is metered at the broker boundary, including HTTP 200
  responses with no data. Three consecutive 401/403s stop requests (one probe
  per 15 minutes); rejected credentials are not retried.
- Sessions are judged on data received. A degraded session is written as such
  to the health report, shadow events and session log, raises a CRITICAL
  alert, is excluded from observation progress, and exits with status 3.
- Sessions on the Tradier provider from 2026-09-22 are excluded by annotation.
- Zero confirmed symbols is STANDBY, never a silent fallback to SPY.

**Reporting**
- The EOD page shows the constrained baseline portfolio replay, labeled,
  instead of a sum of every close and counterfactual.
- The market regime reads completed bars only, as signals do.
- `ledger.json` summary blocks are recomputed; the corrected freeze document
  and the pre-registration (with its outcome addendum) now live here.

**Operations**
- Preflight runs a read-only options-data canary before arming.
- The watchdog alerts during a session that is alive but receiving no options
  data or whose provider rejects authorization.
- Dashboard controls that remove protections or change what runs require
  `DASHBOARD_CONTROL_TOKEN`; activating the kill switch does not. No wildcard CORS.
- Each session records its effective settings (secrets redacted).
- CI has no quarantine: every test is required.

## Host steps (in order)

1. **Stop the session cron now.** Every session until the options provider is
   replaced produces no evidence. Stopping it also keeps the operational
   branch from advancing; if it advances, rebase this release onto it first
   (conflicts are limited to `logs/` and `evaluation/reports/`).
2. **Preserve the preflight that is actually running.** Its output does not
   match any tracked script. Find it from `crontab -l`, copy it off the host,
   and compare it with `scripts/ops/preflight.sh` before the installer
   replaces it.
3. **Configure alerts and prove delivery.** Set `OPS_NTFY_URL` (an HTTPS ntfy
   topic) in `/root/trader-ops.env`, then run
   `.venv/bin/python scripts/session_ops.py test-alert` and confirm the
   notification arrives. The last session log shows the application alert
   service with no channels configured; until this step, nothing pages anyone.
4. If the dashboard is used remotely, set `DASHBOARD_CONTROL_TOKEN` (16+
   characters) in `/root/trader/.env`.
5. Run the installer for this release's commit, per
   `evaluation/operations_rollout_2026_09_21.md`.
6. **Expected result:** with Tradier still configured, `preflight.sh
   --check-only` fails at the options-data canary with a 401, and sends a
   `preflight_failed` alert. That is correct. Leave cron disabled until the
   options provider is replaced (`evaluation/alpaca_options_provider_rollout.md`).

## Not in this release

- Alpaca options-data adapter repairs and the provider switch: branch
  `agent/alpaca-options-feed-repairs`, which changes a frozen adapter and must
  ship with a new cohort declaration.
- Broker-fill reconciliation of historical cohorts (needs the VPS database and
  an Alpaca fill export).
- A replacement strategy hypothesis. The current signals remain the baseline
  any replacement must beat.
